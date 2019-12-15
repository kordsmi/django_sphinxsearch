import functools
from collections import OrderedDict

from django.db import models
from django.db.models import Count, BooleanField
from django.db.models.base import ModelBase
from django.db.models.expressions import Col, BaseExpression
from django.db.models.sql import Query
from django.db.models.sql.where import WhereNode, ExtraWhere
from django.utils.datastructures import OrderedSet


class SphinxTableName(str):
    is_table_name = True


# noinspection PyAbstractClass
class SphinxCount(Count):
    """ Replaces Mysql-like COUNT('*') with COUNT(*) token."""
    template = '%(function)s(*)'

    def as_sql(self, compiler, connection, function=None, template=None):
        sql, params = super().as_sql(
            compiler, connection, function=function, template=template)
        try:
            params.remove('*')
        except ValueError:
            pass
        return sql, params


class SphinxWhereExpression(BaseExpression):
    def __init__(self, where, where_params):
        self.where = where
        self.where_params = where_params
        super().__init__(output_field=BooleanField())

    def as_sql(self, compiler, connection):
        return self.where, self.where_params


class SphinxExtraWhere(ExtraWhere):

    def as_sql(self, qn=None, connection=None):
        sqls = ["%s" % sql for sql in self.sqls]
        return " AND ".join(sqls), tuple(self.params or ())


class SphinxWhereNode(WhereNode):
    pass


class SphinxQuery(Query):
    _clonable = ('options', 'match', 'group_limit', 'group_order_by',
                 'with_meta')

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('where', SphinxWhereNode)
        super().__init__(*args, **kwargs)

    def clone(self):
        query = super().clone()
        for attr_name in self._clonable:
            value = getattr(self, attr_name, None)
            if value:
                setattr(query, attr_name, value)
        return query

    def add_match(self, *args, **kwargs):
        if not hasattr(self, 'match'):
            # noinspection PyAttributeOutsideInit
            self.match = OrderedDict()
        for expression in args:
            self.match.setdefault('*', OrderedSet())
            if isinstance(expression, (list, tuple)):
                self.match['*'].update(expression)
            else:
                self.match['*'].add(expression)
        for field, expression in kwargs.items():
            self.match.setdefault(field, OrderedSet())
            if isinstance(expression, (list, tuple, set)):
                self.match[field].update(expression)
            else:
                self.match[field].add(expression)

    def get_count(self, using):
        """
        Performs a COUNT() query using the current filter constraints.
        """
        obj = self.clone()
        obj.add_annotation(SphinxCount('*'), alias='__count', is_summary=True)
        number = obj.get_aggregation(using, ['__count'])['__count']
        if number is None:
            number = 0
        return number


# noinspection PyAbstractClass
class SphinxCol(Col):
    def as_sql(self, compiler, connection):
        # As column names in SphinxQL couldn't be escaped with `backticks`,
        # simply return column name
        return self.target.column, []


class SphinxModelBase(ModelBase):

    # noinspection PyProtectedMember
    def __new__(mcs, name, bases, attrs, **kwargs):
        manual_pk = None
        # Each field must be monkey-patched with SphinxCol class to prevent
        # `tablename`.`attr` appearing in SQL
        for attr in attrs.values():
            if isinstance(attr, models.Field):
                col_patched = getattr(attr, '_col_patched', False)
                if not col_patched:
                    mcs.patch_col_class(attr)
                if attr.primary_key:
                    manual_pk = attr
        # Force non-autoincrement primary key
        if manual_pk is None:
            attrs['id'] = models.IntegerField(primary_key=True,
                                              verbose_name='ID')

        new_class = super().__new__(mcs, name, bases, attrs, **kwargs)

        # mark db_table to be processed with quote_name
        new_class._meta.db_table = SphinxTableName(new_class._meta.db_table)
        return new_class

    def add_to_class(cls, name, value):
        col_patched = getattr(value, '_col_patched', False)
        if not col_patched and isinstance(value, models.Field):
            cls.patch_col_class(value)
        super().add_to_class(name, value)

    @classmethod
    def patch_col_class(mcs, field):
        @functools.wraps(field.get_col)
        def wrapper(alias, output_field=None):
            col = models.Field.get_col(field, alias, output_field=output_field)
            col.__class__ = SphinxCol
            return col
        field.get_col = wrapper
