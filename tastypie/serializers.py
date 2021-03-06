from django.core.exceptions import ImproperlyConfigured
from django.core.serializers import json
from django.template import loader, Context
from django.utils import simplejson
from django.utils.encoding import force_unicode
from tastypie.exceptions import UnsupportedFormat
from tastypie.representations.simple import Representation, RepresentationSet
from tastypie.utils import format_datetime, format_date, format_time
from tastypie.fields import ApiField, ToOneField, ToManyField
from StringIO import StringIO
import datetime
try:
    import lxml
    from lxml.etree import parse as parse_xml
    from lxml.etree import Element, tostring
except ImportError:
    lxml = None
try:
    import yaml
    from django.core.serializers import pyyaml
except ImportError:
    yaml = None


class Serializer(object):
    formats = ['json', 'jsonp', 'xml', 'yaml', 'html']
    content_types = {
        'json': 'application/json',
        'jsonp': 'text/javascript',
        'xml': 'application/xml',
        'yaml': 'text/yaml',
        'html': 'text/html',
    }
    
    def __init__(self, formats=None, content_types=None):
        self.supported_formats = []
        
        if formats is not None:
            self.formats = formats
        
        if content_types is not None:
            self.content_types = content_types
        
        for format in self.formats:
            try:
                self.supported_formats.append(self.content_types[format])
            except KeyError:
                raise ImproperlyConfigured("Content type for specified type '%s' not found. Please provide it at either the class level or via the arguments." % format)
    
    def get_mime_for_format(self, format):
        try:
            return self.content_types[format]
        except KeyError:
            return 'application/json'
    
    def serialize(self, representation, format='application/json', options={}):
        desired_format = None
        
        for short_format, long_format in self.content_types.items():
            if format == long_format:
                if hasattr(self, "to_%s" % short_format):
                    desired_format = short_format
                    break
        
        if desired_format is None:
            raise UnsupportedFormat("The format indicated '%s' had no available serialization method. Please check your ``formats`` and ``content_types`` on your Serializer." % format)
        
        serialized = getattr(self, "to_%s" % desired_format)(representation, options)
        return serialized
    
    def deserialize(self, content, format='application/json'):
        desired_format = None
        
        for short_format, long_format in self.content_types.items():
            if format == long_format:
                if hasattr(self, "from_%s" % short_format):
                    desired_format = short_format
                    break
        
        if desired_format is None:
            raise UnsupportedFormat("The format indicated '%s' had no available deserialization method. Please check your ``formats`` and ``content_types`` on your Serializer." % format)
        
        deserialized = getattr(self, "from_%s" % desired_format)(content)
        return deserialized

    def to_simple(self, data, options):
        if type(data) in (list, tuple) or isinstance(data, RepresentationSet):
            return [self.to_simple(item, options) for item in data]
        elif isinstance(data, dict):
            return dict((key, self.to_simple(val, options)) for (key, val) in data.iteritems())
        elif isinstance(data, Representation):
            object = {}
            for field_name, field_object in data.fields.items():
                object[field_name] = self.to_simple(field_object, options)
            return object
        elif isinstance(data, ApiField):
            if isinstance(data, ToOneField):
                if data.full_repr:
                    return self.to_simple(data.fk_repr, options)
                else:
                    return self.to_simple(data.value, options)
            elif isinstance(data, ToManyField):
                if data.full_repr:
                    return [self.to_simple(repr, options) for repr in data.m2m_reprs]
                else:
                    return [self.to_simple(val, options) for val in data.value]
            else:
                return self.to_simple(data.value, options)
        elif isinstance(data, datetime.datetime):
            return format_datetime(data)
        elif isinstance(data, datetime.date):
            return format_date(data)
        elif isinstance(data, datetime.time):
            return format_time(data)
        elif isinstance(data, bool):
            return data
        elif type(data) in (long, int, float):
            return data
        elif data is None:
            return None
        else:
            return force_unicode(data)

    def to_etree(self, data, options=None, name=None, depth=0):
        if type(data) in (list, tuple) or isinstance(data, RepresentationSet):
            element = Element(name or 'objects')
            if name:
                element = Element(name)
                element.set('type', 'list')
            else:
                element = Element('objects')
            for item in data:
                element.append(self.to_etree(item, options, depth=depth+1))
        elif isinstance(data, dict):
            if depth == 0:
                element = Element(name or 'response')
            else:
                element = Element(name or 'object')
                element.set('type', 'hash')
            for (key, value) in data.iteritems():
                element.append(self.to_etree(value, options, name=key, depth=depth+1))
        elif isinstance(data, Representation):
            element = Element(name or 'object')
            for field_name, field_object in data.fields.items():
                element.append(self.to_etree(field_object, options, name=field_name, depth=depth+1))
        elif isinstance(data, ApiField):
            if isinstance(data, ToOneField):
                if data.full_repr:
                    return self.to_etree(data.fk_repr, options, name, depth+1)
                else:
                    return self.to_etree(data.value, options, name, depth+1)
            elif isinstance(data, ToManyField):
                if data.full_repr:
                    element = Element(name or 'objects')
                    for repr in data.m2m_reprs:
                        element.append(self.to_etree(repr, options, repr.resource_name, depth+1))
                else:
                    element = Element(name or 'objects')
                    for value in data.value:
                        element.append(self.to_etree(value, options, name, depth=depth+1))
            else:
                return self.to_etree(data.value, options, name)
        else:
            element = Element(name or 'value')
            simple_data = self.to_simple(data, options)
            data_type = get_type_string(simple_data)
            if data_type != 'string':
                element.set('type', get_type_string(simple_data))
            if data_type != 'null':
                element.text = force_unicode(simple_data)
        return element

    def from_etree(self, data):
        """
        Not the smartest deserializer on the planet. At the request level,
        it first tries to output the deserialized subelement called "object"
        or "objects" and falls back to deserializing based on hinted types in
        the XML element attribute "type".
        """
        if data.tag == 'request':
            # if "object" or "objects" exists, return deserialized forms.
            elements = data.getchildren()
            for element in elements:
                if element.tag in ('object', 'objects'):
                    return self.from_etree(element)
            return dict((element.tag, self.from_etree(element)) for element in elements)
        elif data.tag == 'object' or data.get('type') == 'hash':
            return dict((element.tag, self.from_etree(element)) for element in data.getchildren())
        elif data.tag == 'objects' or data.get('type') == 'list':
            return [self.from_etree(element) for element in data.getchildren()]
        else:
            type_string = data.get('type')
            if type_string in ('string', None):
                return data.text
            elif type_string == 'integer':
                return int(data.text)
            elif type_string == 'float':
                return float(data.text)
            elif type_string == 'boolean':
                if data.text == 'True':
                    return True
                else:
                    return False
            else:
                return None
            
    def to_json(self, data, options=None):
        options = options or {}
        data = self.to_simple(data, options)
        return simplejson.dumps(data, cls=json.DjangoJSONEncoder, sort_keys=True)

    def from_json(self, content):
        return simplejson.loads(content)

    def to_jsonp(self, data, options=None):
        options = options or {}
        return '%s(%s)' % (options['callback'], self.to_json(data, options))

    def to_xml(self, data, options=None):
        options = options or {}
        if lxml is None:
            raise ImproperlyConfigured("Usage of the XML aspects requires lxml.")
        return tostring(self.to_etree(data, options), xml_declaration=True, encoding='utf-8')
    
    def from_xml(self, content):
        if lxml is None:
            raise ImproperlyConfigured("Usage of the XML aspects requires lxml.")
        return self.from_etree(parse_xml(StringIO(content)).getroot())
    
    def to_yaml(self, data, options=None):
        options = options or {}
        if yaml is None:
            raise ImproperlyConfigured("Usage of the YAML aspects requires yaml.")
        
        return yaml.dump(self.to_simple(data, options))
    
    def from_yaml(self, content):
        if yaml is None:
            raise ImproperlyConfigured("Usage of the YAML aspects requires yaml.")
        
        return yaml.load(content)
    
    def to_html(self, data, options=None):
        options = options or {}
        pass
    
    def from_html(self, content):
        pass

def get_type_string(data):
    data_type = type(data)
    if data_type in (int, long):
        return 'integer'
    elif data_type == float:
        return 'float'
    elif data_type == bool:
        return 'boolean'
    elif data_type in (list, tuple) or isinstance(data, RepresentationSet):
        return 'list'
    elif data_type == dict:
        return 'hash'
    elif data is None:
        return 'null'
    elif isinstance(data, basestring):
        return 'string'
