import datetime
import json
import time

import pytz

from sphinxsearch.lookups import sphinx_lookups
from django.core import exceptions
from django.db import models


class SphinxField(models.TextField):
    """ Non-selectable indexed string field

    In sphinxsearch config terms, sql_field_string or rt_field.
    """
    class_lookups = sphinx_lookups.copy()


class SphinxDateTimeField(models.FloatField):
    """ Sphinx timestamp field for sql_attr_timestamp and rt_attr_timestamp.

    NB: sphinxsearch doesn't store microseconds, if necessary, describe
        field as sql_attr_float in config.
    """

    def get_prep_value(self, value):
        if isinstance(value, (datetime.datetime, datetime.date)):
            if value.tzinfo is not None:
                value = pytz.UTC.normalize(value)
            return int(time.mktime(value.timetuple()))
        elif isinstance(value, (int, float)):
            return value
        else:
            raise ValueError("Invalid value for UNIX_TIMESTAMP")

    # noinspection PyMethodMayBeStatic,PyUnusedLocal
    def from_db_value(self, value, expression, connection):
        return datetime.datetime.fromtimestamp(value).replace(tzinfo=pytz.UTC)


class SphinxIntegerField(models.IntegerField):
    class_lookups = sphinx_lookups.copy()


class SphinxBigIntegerField(models.BigIntegerField):
    class_lookups = sphinx_lookups.copy()


class SphinxMultiField(models.IntegerField):
    class_lookups = sphinx_lookups.copy()

    def get_prep_value(self, value):
        if value is None:
            return None
        if isinstance(value, int):
            return value
        get_prep_value = super().get_prep_value
        return [get_prep_value(v) for v in value]

    # noinspection PyUnusedLocal
    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        if isinstance(value, bytes):
            value = value.decode('utf-8')
        if value == '':
            return []
        try:
            return list(map(int, value.split(',')))
        except (TypeError, ValueError):
            raise exceptions.ValidationError(
                self.error_messages['invalid'],
                code='invalid',
                params={'value': value},
            )

    def to_python(self, value):
        if value is None:
            return value
        try:
            return list(map(int, value.split(',')))
        except (TypeError, ValueError):
            raise exceptions.ValidationError(
                self.error_messages['invalid'],
                code='invalid',
                params={'value': value},
            )


class SphinxMulti64Field(SphinxMultiField):
    pass


class SphinxJSONField(models.TextField):

    # noinspection PyUnusedLocal,PyMethodMayBeStatic
    def from_db_value(self, value, expression, connection):
        if not isinstance(value, str) or value is None:
            return value
        return json.loads(value)

    def to_python(self, value):
        if value is None:
            return value
        return json.dumps(value)
