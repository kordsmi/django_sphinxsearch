# coding: utf-8
import re

from django.core.exceptions import FieldError, EmptyResultSet
from django.db import models
from django.db.models.expressions import Random
from django.db.models.lookups import Exact
from django.db.models.sql import compiler, AND
from django.db.models.sql.constants import ORDER_DIR
# noinspection PyProtectedMember
from django.db.models.sql.query import get_order_dir

from sphinxsearch import sql as sqls


class SphinxQLCompiler(compiler.SQLCompiler):
    # Options names that are not escaped by compiler. Don't pass user input
    # there.
    safe_options = ('ranker', 'field_weights', 'index_weights')

    def get_order_by(self):
        res = super().get_order_by()

        order_by = []
        for expr, params in res:
            if isinstance(expr.expression, Random):
                # Replacing ORDER BY RAND() ASC to ORDER BY RAND()
                assert params[0] == 'RAND() ASC', "Expected ordering clause"
                params = ('RAND()',) + params[1:]
            order_by.append((expr, params))
        return order_by

    def get_group_by(self, select, order_by):
        res = super().get_group_by(select, order_by)

        # override GROUP BY columns for sphinxsearch's "GROUP N BY" support
        group_by = getattr(self.query, 'group_by', None)
        if group_by:
            # noinspection PyProtectedMember
            fields = self.query.model._meta.fields
            field_columns = [f.column for f in fields if f.attname in group_by]
            return [r for r in res if r[0] in field_columns]

        return res

    @staticmethod
    def _quote(s, negative=True):
        """ Adds quotes and negates to match lookup expressions."""
        prefix = '-' if negative else ''
        if s.startswith('"'):
            return s
        negative = s.startswith('-')
        if not negative:
            return '"%s"' % s
        s = s[1:]
        if s.startswith('"'):
            return '%s%s' % (prefix, s)
        return '%s"%s"' % (prefix, s)

    def _serialize(self, values_list):
        """ Serializes list of sphinx MATCH lookup expressions

        :param values_list: list of match lookup expressions
        :type values_list: str, list, tuple
        :return: MATCH expression
        :rtype: str
        """""
        if isinstance(values_list, str):
            return values_list

        def ensure_list(s):
            return [s] if isinstance(s, str) else s
        values_list = [item for s in values_list for item in ensure_list(s)]
        positive_list = filter(lambda s: not s.startswith('-'), values_list)
        negative_list = filter(lambda s: s.startswith('-'), values_list)

        positive = "|".join(map(self._quote, positive_list))
        if not positive_list:
            negative = '|'.join(self._quote(n, negative=False)
                                for n in negative_list)
            template = '%s -(%s)'
        else:
            negative = ' '.join(map(self._quote, negative_list))
            template = '%s %s'
        result = template % (positive.strip(' '), negative.strip(' '))
        return result.strip(' ')

    def as_sql(self, with_limits=True, with_col_aliases=False, subquery=False):
        """ Patching final SQL query."""
        query = self.query
        try:
            where_sql, where_params = query.where.as_sql(self, self.connection)
        except EmptyResultSet:
            # Where node compiled to always-false condition, but we still need
            # to call pre_sql_setup() and other methods by super().as_sql
            pass
        else:
            # creating WHERE node with sphinx-aware node implementation if
            # everything is fine
            where = sqls.SphinxWhereNode()
            # Adding MATCH() node to WHERE node if needed
            self._add_match_extra(query, where)
            query.where = where
            # Adding where conditions to SELECT clause because of better
            # support of SQL expressions in sphinxsearch.
            self._add_where_result(query, where_sql, where_params)

        sql, args = super().as_sql(with_limits, with_col_aliases)

        # empty SQL doesn't need patching
        if (sql, args) == ('', ()):
            return sql, args

        # removing unsupported by sphinxsearch OFFSET clause
        # replacing it with LIMIT <offset>, <limit>
        sql = re.sub(r'LIMIT (\d+) OFFSET (\d+)$', 'LIMIT \\2, \\1', sql)

        # patching GROUP BY clause
        group_limit = getattr(query, 'group_limit', '')
        group_by_ordering = self.get_group_ordering()
        if group_limit:
            # add GROUP <N> BY expression
            group_by = 'GROUP %s BY \\1' % group_limit
        else:
            group_by = 'GROUP BY \\1'
        if group_by_ordering:
            # add WITHIN GROUP ORDER BY expression
            group_by += group_by_ordering
        sql = re.sub(r'GROUP BY ((\w+)(, \w+)*)', group_by, sql)

        # adding sphinxsearch OPTION clause
        options = getattr(query, 'options', None)
        if options:
            keys = sorted(options.keys())
            values = [options[k] for k in keys if k not in self.safe_options]

            opts = []
            for k in keys:
                if k in self.safe_options:
                    opts.append("%s=%s" % (k, options[k]))
                else:
                    opts.append("%s=%%s" % k)
            sql += ' OPTION %s' % ', '.join(opts) or ''
            args += tuple(values)
        return sql, args

    def _add_where_result(self, query, where_sql, where_params):
        # Without annotation queryset.count() receives 1 as where_result
        # and count it as aggregation result.
        if where_sql:
            query.add_annotation(
                sqls.SphinxWhereExpression(where_sql, where_params),
                '__where_result')
            # almost all where conditions are now in SELECT clause, so
            # WHERE should contain only test against that conditions are true
            query.add_extra(
                None, None,
                ['__where_result = %s'], (True,), None, None)

    def get_group_ordering(self):
        """ Returns group ordering clause.

        Formats WITHIN GROUP ORDER BY expression
        with columns in query.group_order_by
        """
        group_order_by = getattr(self.query, 'group_order_by', ())
        asc, desc = ORDER_DIR['ASC']
        if not group_order_by:
            return ''
        result = []
        for order_by in group_order_by:
            col, order = get_order_dir(order_by, asc)
            result.append("%s %s" % (col, order))
        return " WITHIN GROUP ORDER BY " + ", ".join(result)

    def _add_match_extra(self, query, where):
        """ adds MATCH clause to query.where """
        # Adding match node if needed
        match = getattr(query, 'match', None)
        if match is None:
            return
        # add match extra where
        expression = []
        all_field_expr = []
        all_fields_lookup = match.get('*')

        # format expression to MATCH against any indexed fields
        if all_fields_lookup:
            if isinstance(all_fields_lookup, str):
                expression.append(all_fields_lookup)
                all_field_expr.append(all_fields_lookup)
            else:
                for value in all_fields_lookup:
                    value_str = self._serialize(value)
                    expression.append(value_str)
                    all_field_expr.append(value_str)

        # format expressions to MATCH against concrete fields
        for sphinx_attr, lookup in match.items():
            if sphinx_attr == '*':
                continue
            # noinspection PyProtectedMember
            field = query.model._meta.get_field(sphinx_attr)
            db_column = field.db_column or field.attname
            expression.append('@' + db_column)
            expression.append("(%s)" % self._serialize(lookup))

        # handle non-ascii characters in search expressions
        def decode(s):
            return s.decode("utf-8") if type(s) is bytes else s
        match_expr = u"MATCH('%s')" % u' '.join(map(decode, expression))

        # add MATCH() to query.where
        where.add(sqls.SphinxExtraWhere([match_expr], []), AND)


# Set SQLCompiler appropriately, so queries will use the correct compiler.
SQLCompiler = SphinxQLCompiler


class SQLInsertCompiler(compiler.SQLInsertCompiler, SphinxQLCompiler):

    def as_sql(self):
        sql = super().as_sql()
        return sql


class SQLDeleteCompiler(compiler.SQLDeleteCompiler, SphinxQLCompiler):
    # noinspection PyMethodOverriding
    def as_sql(self):
        sql, params = super().as_sql()

        # empty SQL doesn't need patching
        if (sql, params) == ('', ()):
            return sql, params

        sql = re.sub(r'\(IN\((\w+),\s([\w\s%,]+)\)\)', '\\1 IN (\\2)', sql)
        return sql, params


class SQLUpdateCompiler(compiler.SQLUpdateCompiler, SphinxQLCompiler):

    # noinspection PyMethodOverriding
    def as_sql(self):
        node = self.is_single_row_update()
        # determine whether use UPDATE (only fixed-length fields) or
        # REPLACE (internal delete + insert) syntax
        need_replace = False
        if node:
            need_replace = self._has_string_fields()
        if node and need_replace:
            sql, args = self.as_replace(node)
        else:
            self._add_match_extra(self.query, self.query.where)
            sql, args = super().as_sql()
        return sql, args

    def is_single_row_update(self):
        where = self.query.where
        match = getattr(self.query, 'match', {})
        node = None
        if len(where.children) == 1:
            node = where.children[0]
        elif match:
            # noinspection PyProtectedMember
            meta = self.query.model._meta
            pk_match = match.get(meta.pk.attname)
            if pk_match is not None:
                pk_value = list(pk_match.dict.keys())[0]
                return Exact(meta.pk.get_col(meta.db_table), pk_value)
        if not isinstance(node, Exact):
            node = None
        elif not node.lhs.field.primary_key:
            node = None
        return node

    def as_replace(self, where_node):
        """
        Performs single-row UPDATE as REPLACE INTO query.

        Must be used to change string attributes or indexed fields.
        """

        # It's a copy of compiler.SQLUpdateCompiler.as_sql method
        # that formats query more like INSERT than UPDATE
        self.pre_sql_setup()
        if not self.query.values:
            return '', ()
        table = self.query.base_table
        qn = self.quote_name_unless_alias
        result = ['REPLACE INTO %s' % qn(table)]
        # noinspection PyProtectedMember
        meta = self.query.model._meta
        self.query.values.append((meta.pk, self.query.model, where_node.rhs))
        columns, values, update_params = [], [], []

        for field, model, val in self.query.values:
            if hasattr(val, 'resolve_expression'):
                val = val.resolve_expression(self.query, allow_joins=False,
                                             for_save=True)
                if val.contains_aggregate:
                    raise FieldError(
                        "Aggregate functions are not allowed in this query")
            elif hasattr(val, 'prepare_database_save'):
                if field.rel:
                    val = field.get_db_prep_save(
                        val.prepare_database_save(field),
                        connection=self.connection,
                    )
                else:
                    raise TypeError(
                        "Database is trying to update a relational field "
                        "of type %s with a value of type %s. Make sure "
                        "you are setting the correct relations" %
                        (field.__class__.__name__, val.__class__.__name__))
            else:
                val = field.get_db_prep_save(val, connection=self.connection)

            # Getting the placeholder for the field.
            if hasattr(field, 'get_placeholder'):
                placeholder = field.get_placeholder(val, self, self.connection)
            else:
                placeholder = '%s'
            name = field.column
            columns.append(qn(name))
            if hasattr(val, 'as_sql'):
                sql, params = self.compile(val)
                values.append(sql)
                update_params.extend(params)
            elif val is not None:
                values.append(placeholder)
                update_params.append(val)
            else:
                values.append('NULL')
        if not values:
            return '', ()
        result.append('(')
        result.append(', '.join(columns))
        result.append(') VALUES (')
        result.append(', '.join(values))
        result.append(')')
        return ' '.join(result), tuple(update_params)

    def _has_string_fields(self):
        """ check whether query is updating text fields."""
        _excluded_update_fields = (
            models.CharField,
            models.TextField
        )
        for field, model, val in self.query.values:
            if isinstance(field, _excluded_update_fields):
                return True
        return False


class SQLAggregateCompiler(compiler.SQLAggregateCompiler, SphinxQLCompiler):
    pass
