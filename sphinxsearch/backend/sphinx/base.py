import sys
import warnings
from collections import OrderedDict

from django.db import ProgrammingError
from django.db.backends.mysql import base, creation
from django.db.backends.mysql.base import server_version_re
from django.utils.functional import cached_property


class SphinxOperations(base.DatabaseOperations):

    compiler_module = "sphinxsearch.backend.sphinx.compiler"

    def json_cast_text_sql(self, field_name):
        pass

    def regex_lookup(self, lookup_type):
        raise NotImplementedError()

    def force_no_ordering(self):
        """ Fix unsupported syntax "ORDER BY NULL"."""
        return []

    def quote_name(self, name):
        """ Table names are prefixed with database name."""
        if getattr(name, 'is_table_name', False):
            db_name = self.connection.settings_dict.get('NAME', '')
            if db_name:
                name = '%s___%s' % (db_name, name)
        return super().quote_name(name)


class SphinxValidation(base.DatabaseValidation):
    def _check_sql_mode(self, **kwargs):
        """ Disable sql_mode validation because it's unsupported
        >>> import django.db
        >>> cursor = django.db.connection
        >>> cursor.execute("SELECT @@sql_mode")
        # Error here after parsing searchd response
        """
        return []


class SphinxCreation(creation.DatabaseCreation):

    def __no_db_cursor(self):
        try:
            return self._nodb_connection.cursor()  # noqa
        except AttributeError:
            # Django-3.1+
            return self._nodb_cursor()  # noqa

    def create_test_db(self, *args, **kwargs):
        # NOOP, test using regular sphinx database.
        if self.connection.settings_dict.get('TEST_NAME'):
            # initialize connection database name
            test_name = self.connection.settings_dict['TEST_NAME']
            self.connection.close()
            self.connection.settings_dict['NAME'] = test_name
            self.connection.cursor()
            return test_name
        return self.connection.settings_dict['NAME']

    def destroy_test_db(self, *args, **kwargs):
        # NOOP, we created nothing, nothing to destroy.
        return

    def clone_test_db(self, suffix, verbosity=1, autoclobber=False,
                      keepdb=False):
        """
        Sphinxsearch does not support databases, so just copying all tables
        with source prefix to dest prefixes new ones."""
        src_db_name = self.connection.settings_dict['NAME']
        src_prefix = f'{src_db_name}___'
        sys.stdout.write(
            f"Cloning sphinxsearch tables for alias {src_db_name}...\n")
        with self.__no_db_cursor() as cursor:
            cursor.execute("SHOW TABLES")
            for table_name, index_type in cursor.fetchall():
                if not table_name.startswith(src_prefix):
                    continue
                self._clone_table(table_name, suffix, keepdb=keepdb)

    def _clone_table(self, table_name, suffix, keepdb=False):
        src_db_name = self.connection.settings_dict['NAME']
        attr_types = {
            'uint': 'integer',
            'timestamp': 'integer',
            'mva': 'multi',
            'mva64': 'multi64',
        }
        _, main_table_name = table_name.split('___', 1)
        new_table_name = f'{src_db_name}_{suffix}___{main_table_name}'
        with self.__no_db_cursor() as cursor:
            cursor.execute(f"DESCRIBE {table_name}")
            sql = [f"CREATE TABLE {new_table_name} ("]
            columns = OrderedDict()
            for name, attr_type, properties, key in cursor.fetchall():
                if name == 'id':
                    continue
                attr_type = attr_types.get(attr_type, attr_type)
                properties = set(properties.split(','))
                if name not in columns:
                    columns[name] = [attr_type, properties]
                else:
                    types = {columns[name][0], attr_type}
                    if types != {'field', 'string'}:
                        raise RuntimeError("Dont know how to deal with it")
                    columns[name][0] = 'field'
                    columns[name][1].update({'indexed', 'stored'})

            column_defs = []
            for name, (attr_type, properties) in columns.items():
                column_defs.append(
                    f'{name} {attr_type} {" ".join(properties)}')

            sql.append(',\n'.join(column_defs))
            sql.append(')')
            try:
                cursor.execute(' '.join(sql))
            except ProgrammingError as e:
                if e.args[-1].endswith('already exists'):
                    if keepdb:
                        return
                    # noinspection SqlResolve
                    cursor.execute(
                        f"DROP TABLE {new_table_name}")
                    cursor.execute(' '.join(sql))
                else:  # pragma: no cover
                    raise


class SphinxFeatures(base.DatabaseFeatures):
    # The following can be useful for unit testing, with multiple databases
    # configured in Django, if one of them does not support transactions,
    # Django will fall back to using clear/create
    # (instead of begin...rollback) between each test. The method Django
    # uses to detect transactions uses CREATE TABLE and DROP TABLE,
    # which ARE NOT supported by Sphinx, even though transactions ARE.
    # Therefore, we can just set this to True, and Django will use
    # transactions for clearing data between tests when all OTHER backends
    # support it.
    supports_transactions = True
    allows_group_by_pk = False
    uses_savepoints = True
    supports_column_check_constraints = False
    is_sql_auto_is_null_enabled = False


class DatabaseWrapper(base.DatabaseWrapper):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ops = SphinxOperations(self)
        self.creation = SphinxCreation(self)
        self.features = SphinxFeatures(self)
        self.validation = SphinxValidation(self)

    @cached_property
    def mysql_version(self):
        # Django>=1.10 makes if differently
        with self.temporary_connection():
            server_info = self.connection.get_server_info()
        match = server_version_re.match(server_info)
        if not match:
            raise Exception('Unable to determine MySQL version from version '
                            'string %r' % server_info)
        return tuple(int(x) for x in match.groups())

    def _savepoint(self, sid):
        """ Stub for savepoints.

        Sphinx does not support transactions Django knows, so we have to clean
        Sphinx database by ourselves between tests. But we don't want tell
        Django about it, because it will not be using them for another
        databases as we need. So we tell Django we support transaction and
        savepoints.
        When transactions are active Django does rollback to a savepoint after
        each test. So we need this and two stubs below that Django
        thinks everything is fine."""
        warnings.warn("Sphinx warning: Sphinx doesn't support savepoints, "
                      "only emulates them. Savepoint creation is not done.")

    def _savepoint_rollback(self, sid):
        warnings.warn("Sphinx warning: Sphinx doesn't support savepoints, "
                      "only emulates them. Rollback is not done.")

    def _savepoint_commit(self, sid):
        warnings.warn("Sphinx warning: Sphinx doesn't support savepoints, "
                      "only emulates them. Commit is not done.")
