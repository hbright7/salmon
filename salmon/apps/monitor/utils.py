import json
import logging
import subprocess

from django.conf import settings
from django.db import connection


logger = logging.getLogger(__name__)


def get_latest_results(minion=None, check_ids=None):
    """
    For each (or all) minions and for the given check_ids
    get the newest result for every active check
    """
    from . import models
    cursor = connection.cursor()
    if not check_ids:
        check_ids = (models.Check.objects.filter(active=True)
                                         .values_list('pk', flat=True))

    if check_ids:
        if len(check_ids) == 1:
            having = "check_id = {0}".format(check_ids[0])
        else:
            having = "check_id IN {0}".format(tuple(check_ids))
        if minion:
            having += " AND minion_id={0}".format(minion.pk)
        # we need a first query to get the latest batch of results
        latest_timestamps = cursor.execute("""
            SELECT minion_id, check_id, MAX("timestamp")
            FROM "monitor_result"
            GROUP BY "monitor_result"."minion_id", "monitor_result"."check_id"
            HAVING {0};""".format(having))

        data = latest_timestamps.fetchall()
        if data:
            # transform the result to group them by minion_id, check_id,
            # timestamp # the new form of latest_timestamps can easily be
            # consumed by Result ORM
            latest_timestamps = zip(*data)
            latest_results = models.Result.objects.filter(
                minion_id__in=latest_timestamps[0],
                check_id__in=latest_timestamps[1],
                timestamp__in=latest_timestamps[2])
    else:
        latest_results = []
    return latest_results


class SaltProxy(object):

    def __init__(self, target, function, output="json"):
        self.target = target
        self.function = function
        self.output = output
        self.cmd = self._build_command(output=output)

    def _build_command(self, output='json'):
        # FIXME: this is a bad way to build up the command
        if settings.SALT_COMMAND.startswith('ssh'):
            quote = '\\\"'
        else:
            quote = '"'
        args = '--static --out={output} {quote}{target}{quote} {function}'.format(
            output=self.output, quote=quote,
            target=self.target, function=self.function)
        cmd = settings.SALT_COMMAND.format(args=args)
        return cmd

    def run(self):
        try:
            result = subprocess.Popen(self._build_command(),
                                      shell=True,
                                      stdout=subprocess.PIPE).communicate()[0]
            return json.loads(result)
        except ValueError as err:
            logging.exception("Error parsing results.")


def parse_value(raw_value, opts):
    value = raw_value
    if 'key' in opts:
        key_tree = opts['key'].split('.')
        for key in key_tree:
            value = value[key]
    # Handle the special case where the value is None
    elif value is None:
        value = ""
    return value


def check_failed(value, opts):
    checker = Checker(cast_to=opts['type'], raw_value=value)
    return not checker.do_assert(opts['assert'])


class Checker(object):
    def __init__(self, cast_to, raw_value):
        self.cast_to = cast_to
        self.raw_value = raw_value
        self.value = self.cast()

    def cast(self):
        if not hasattr(self, "value"):
            self.value = getattr(
                self, 'to_{0}'.format(self.cast_to))(self.raw_value)
        return self.value

    def do_assert(self, assertion_string):
        # TODO: try to remove the evil
        success = eval(assertion_string.format(value=self.value))
        assert isinstance(success, bool)
        return success

    def to_boolean(self, value):
        # bool('False') == True
        if value == "False":
            return False
        return bool(value) is True

    def to_percentage(self, value):
        return self.to_float(value)

    def to_percentage_with_sign(self, value):
        return self.to_float(value.rstrip('%'))

    def to_float(self, value):
        return float(value)

    def to_string(self, value):
        return str(value)
