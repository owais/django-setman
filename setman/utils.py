import logging
import os

from ConfigParser import ConfigParser, Error as ConfigParserError
from decimal import Decimal

from django import forms
from django.conf import settings as django_settings
from django.utils import importlib


__all__ = ('AVAILABLE_SETTINGS', 'parse_config')


DEFAULT_SETTINGS_FILENAME = 'settings.cfg'
logger = logging.getLogger('setman')


class Setting(object):
    """
    Base class for setting values that can provided in configuration definition
    file.
    """
    default = None
    field_args = ('label', 'help_text', 'initial', 'validators')
    field_klass = None
    field_kwargs = {}
    help_text = None
    label = None
    name = None
    type = None
    validators = None

    def __init__(self, **kwargs):
        restricted = ('field_klass', 'field_args', 'field_kwargs')

        for key, _ in kwargs.items():
            if not hasattr(self, key):
                kwargs.pop(key)

            if key in restricted:
                kwargs.pop(key)

        self.__dict__.update(kwargs)
        self.validators = self._parse_validators(self.validators)

    def __repr__(self):
        return u'<%s: %s>' % (self.__class__.__name__, self.__unicode__())

    def __unicode__(self):
        return u'%s = %r' % (self.name, self.initial)

    @property
    def initial(self):
        """
        Read real setting value from database or if impossible - just send
        default setting value.
        """
        from setman import settings
        return getattr(settings, self.name, self.default)

    def to_field(self):
        """
        Convert current setting instance to form field.
        """
        if not self.field_klass:
            raise ValueError('Please, supply `field_klass` attribute first.')

        kwargs = {}

        for arg in self.field_args:
            kwargs.update({arg: getattr(self, arg)})

        kwargs.update(**self.field_kwargs)
        return self.field_klass(**kwargs)

    def to_python(self, value):
        """
        Convert setting value to necessary Python type. By default, returns
        same value without any conversion.
        """
        return value

    def _parse_validators(self, value):
        """
        Parse validators string and try to convert it to list with actual
        validator functions.
        """
        if not value:
            return []

        items = map(lambda item: item.strip(), value.split(','))
        validators = []

        for item in items:
            module, attr = item.rsplit('.', 1)

            try:
                mod = importlib.import_module(module)
            except ImportError:
                logger.exception('Cannot load %r validator for %s setting.',
                                 item, self.name)
                continue

            try:
                validator = getattr(mod, attr)
            except AttributeError:
                logger.exception('Cannot load %r validator for %s setting.',
                                 item, self.name)
                continue

            validators.append(validator)

        return validators


class SettingTypeDoesNotExist(Exception):
    """
    Simple exception that raised when user tried to load not supported setting
    type from configuration definition file.
    """


class BooleanSetting(Setting):
    """
    Boolean setting.
    """
    field_klass = forms.BooleanField
    field_kwargs = {'required': False}
    type = 'boolean'

    def __init__(self, **kwargs):
        super(BooleanSetting, self).__init__(**kwargs)
        self.default = self.to_python(self.default)

    def to_python(self, value):
        """
        Convert string to the boolean type.
        """
        if isinstance(value, (bool, int)):
            return bool(value)

        boolean_states = ConfigParser._boolean_states
        if not value.lower() in boolean_states:
            return None

        return boolean_states[value.lower()]


class ChoiceSetting(Setting):
    """
    Choice setting.
    """
    choices = None
    field_args = Setting.field_args + ('choices', )
    field_klass = forms.ChoiceField
    type = 'choice'

    def __init__(self, **kwargs):
        super(ChoiceSetting, self).__init__(**kwargs)
        self.choices = self.build_choices(self.choices)

    def build_choices(self, value):
        return tuple(map(lambda s: s.strip(), value.split(',')))

    def to_field(self):
        old_choices = self.choices
        self.choices = [(choice, choice) for choice in old_choices]

        field = super(ChoiceSetting, self).to_field()
        self.choices = old_choices

        return field


class DecimalSetting(Setting):
    """
    Decimal setting.
    """
    decimal_places = None
    field_args = Setting.field_args + ('decimal_places', 'max_digits',
                                       'max_value', 'min_value')
    field_klass = forms.DecimalField
    max_digits = None
    max_value = None
    min_value = None
    type = 'decimal'

    def __init__(self, **kwargs):
        super(DecimalSetting, self).__init__(**kwargs)

        int_setting = IntSetting()
        self.decimal_places = int_setting.to_python(self.decimal_places)
        self.max_digits = int_setting.to_python(self.max_digits)

        self.default = self.to_python(self.default)
        self.max_value = self.to_python(self.max_value)
        self.min_value = self.to_python(self.min_value)

    def to_python(self, value):
        if value is None:
            return value
        return Decimal(value)


class IntSetting(Setting):
    """
    Integer setting.
    """
    field_args = Setting.field_args + ('max_value', 'min_value')
    field_klass = forms.IntegerField
    max_value = None
    min_value = None
    type = 'int'

    def __init__(self, **kwargs):
        super(IntSetting, self).__init__(**kwargs)
        self.default = self.to_python(self.default)
        self.max_value = self.to_python(self.max_value)
        self.min_value = self.to_python(self.min_value)

    def to_python(self, value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


class FloatSetting(IntSetting):
    """
    Float setting.
    """
    field_klass = forms.FloatField
    type = 'float'

    def to_python(self, value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


class StringSetting(Setting):
    """
    String setting.
    """
    field_args = Setting.field_args + ('max_length', 'min_length')
    field_klass = forms.CharField
    max_length = None
    min_length = None
    regex = None
    type = 'string'


class SettingsContainer(object):

    def __init__(self, path):
        self._data = []
        self.path = path

    def __iter__(self):
        return (item for item in self._data)

    def __len__(self):
        return len(self._data)

    def append(self, value):
        self._data.append(value)
        setattr(self, value.name, value)


def data_to_setting(data):
    """
    Convert data dict to setting instance.
    """
    setting = None
    setting_type = data.get('type')

    for value in globals().values():
        try:
            if not issubclass(value, Setting):
                continue
        except TypeError:
            continue

        if not value.type or value.type != setting_type:
            continue

        setting = value(**data)

    if setting is None:
        raise SettingTypeDoesNotExist('%r setting type not found.' % \
                                      setting_type)

    return setting


def parse_config(path=None):
    """
    Parse Configuration Definition File.

    In most cases this file needs to be placed in same folder where project
    settings module exist and named as ``settings.cfg``.

    But you can customize things with using ``SETMAN_SETTINGS_FILE`` option.
    Provide there path where settings file actually placed.

    Also current function can called with ``path`` string.
    """
    if path is None:
        path = getattr(django_settings, 'SETMAN_SETTINGS_FILE', None)

        if path is None:
            module = importlib.import_module(django_settings.SETTINGS_MODULE)
            dirname = os.path.dirname(os.path.normpath(module.__file__))
            path = os.path.join(dirname, DEFAULT_SETTINGS_FILENAME)

    if not os.path.isfile(path):
        logger.error('Cannot read configuration definition file at %r. Exit ' \
                     'from parsing!', path)
        return []

    config = ConfigParser()

    try:
        config.read(path)
    except ConfigParserError:
        logger.exception('Cannot parse configuration definition file from ' \
                         '%r', path)
        return []

    settings = SettingsContainer(path)

    for setting in config.sections():
        data = dict(config.items(setting))
        data.update({'name': setting})

        try:
            setting = data_to_setting(data)
        except SettingTypeDoesNotExist:
            logger.exception('Cannot find proper setting class for %r type',
                             data.get('type'))
            return []

        settings.append(setting)

    return settings


AVAILABLE_SETTINGS = parse_config()
