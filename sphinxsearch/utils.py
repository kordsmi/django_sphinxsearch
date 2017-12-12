# coding: utf-8

# $Id: $
import re


def sphinx_escape(value):
    """ Escapes SphinxQL search expressions. """

    if not isinstance(value, str):
        return value

    value = re.sub(r"([=<>()|!@~&/^$\-\'\"\\])", r'\\\\\\\1', value)
    value = re.sub(r'\b(SENTENCE|PARAGRAPH)\b', r'\\\\\\\1', value)
    return value
