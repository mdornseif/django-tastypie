from django.conf.urls.defaults import patterns, url
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from tastypie.authentication import Authentication
from tastypie.cache import NoCache
from tastypie.exceptions import NotFound, BadRequest, MultipleRepresentationsFound
from tastypie.http import *
from tastypie.paginator import Paginator
from tastypie.serializers import Serializer
from tastypie.throttle import BaseThrottle
from tastypie.utils import is_valid_jsonp_callback_value
from tastypie.utils.mime import determine_format, build_content_type


class Resource(object):
    """
    Mostly for dispatch and responding to requests.
    
    The business logic of what data is available is covered by the
    ``Representation`` object.
    
    Serialization/deserialization is handled "at the edges" (i.e. at the
    beginning/end of the request/response cycle) so that everything internally
    is Python data structures.
    """
    representation = None
    list_representation = None
    detail_representation = None
    serializer = Serializer()
    authentication = Authentication()
    cache = NoCache()
    throttle = BaseThrottle()
    allowed_methods = None
    list_allowed_methods = ['get', 'post', 'put', 'delete']
    detail_allowed_methods = ['get', 'post', 'put', 'delete']
    limit = 20
    api_name = 'nonspecific'
    resource_name = None
    default_format = 'application/json'
    
    def __init__(self, representation=None, list_representation=None,
                 detail_representation=None, serializer=None,
                 authentication=None, allowed_methods=None,
                 list_allowed_methods=None, detail_allowed_methods=None,
                 limit=None, resource_name=None, api_name=None):
        # Shortcut to specify both via arguments.
        if representation is not None:
            self.representation = representation
        
        # Shortcut to specify both at the class level.
        if self.representation is not None:
            self.list_representation = self.representation
            self.detail_representation = self.representation
        
        if list_representation is not None:
            self.list_representation = list_representation
        
        if detail_representation is not None:
            self.detail_representation = detail_representation
        
        if serializer is not None:
            self.serializer = serializer
        
        if authentication is not None:
            self.authentication = authentication
        
        # Shortcut to specify both via arguments.
        if allowed_methods is not None:
            self.allowed_methods = allowed_methods
        
        # Shortcut to specify both at the class level.
        if self.allowed_methods is not None:
            self.list_allowed_methods = self.allowed_methods
            self.detail_allowed_methods = self.allowed_methods
        
        if list_allowed_methods is not None:
            self.list_allowed_methods = list_allowed_methods
        
        if detail_allowed_methods is not None:
            self.detail_allowed_methods = detail_allowed_methods
        
        if limit is not None:
            self.limit = limit
        
        if resource_name is not None:
            self.resource_name = resource_name
        
        if api_name is not None:
            self.api_name = api_name
        
        # Make sure we're good to go.
        if self.list_representation is None:
            raise ImproperlyConfigured("No general representation or specific list representation provided for %r." % self)
        
        if self.detail_representation is None:
            raise ImproperlyConfigured("No general representation or specific detail representation provided for %r." % self)
        
        if self.serializer is None:
            raise ImproperlyConfigured("No serializer provided for %r." % self)
        
        if not self.resource_name:
            raise ImproperlyConfigured("No resource_name provided for %r." % self)
    
    def wrap_view(self, view):
        def wrapper(request, *args, **kwargs):
            return getattr(self, view)(request, *args, **kwargs)
        return wrapper
    
    @property
    def urls(self):
        urlpatterns = patterns('',
            url(r"^(?P<resource_name>%s)/$" % self.resource_name, self.wrap_view('dispatch_list'), name="api_dispatch_list"),
            url(r"^(?P<resource_name>%s)/schema/$" % self.resource_name, self.wrap_view('get_schema'), name="api_get_schema"),
            url(r"^(?P<resource_name>%s)/set/(?P<id_list>[^/]+)/$" % self.resource_name, self.wrap_view('get_multiple'), name="api_get_multiple"),
            url(r"^(?P<resource_name>%s)/(?P<obj_id>[^/;]+)/$" % self.resource_name, self.wrap_view('dispatch_detail'), name="api_dispatch_detail"),
        )
        return urlpatterns
    
    def determine_format(self, request):
        return determine_format(request, self.serializer, default_format=self.default_format)

    def serialize(self, request, data, format, options=None):
        options = options or {}

        if 'text/javascript' in format:
            # get JSONP callback name. default to "callback"
            callback = request.GET.get('callback', 'callback')
            if not is_valid_jsonp_callback_value(callback):
                raise BadRequest('JSONP callback name is invalid.')
            options['callback'] = callback

        return self.serializer.serialize(data, format, options)

    def deserialize(self, request, data, format='application/json'):
        return self.serializer.deserialize(data, format=request.META.get('CONTENT_TYPE', 'application/json'))
    
    def dispatch_list(self, request, **kwargs):
        return self.dispatch('list', request, **kwargs)
    
    def dispatch_detail(self, request, **kwargs):
        return self.dispatch('detail', request, **kwargs)
    
    def dispatch(self, request_type, request, **kwargs):
        request_method = request.method.lower()
        allowed_methods = getattr(self, "%s_allowed_methods" % request_type)
        
        if not request_method in allowed_methods:
            return HttpMethodNotAllowed()
        
        method = getattr(self, "%s_%s" % (request_method, request_type), None)
        
        if method is None:
            return HttpNotImplemented()
        
        # Authenticate the request as needed.
        auth_result = self.authentication.is_authenticated(request)
        
        if isinstance(auth_result, HttpResponse):
            return auth_result
        
        if not auth_result is True:
            return HttpUnauthorized()
        
        # Check to see if they should be throttled.
        if self.throttle_check(request):
            # Throttle limit exceeded.
            return HttpForbidden()
        
        # All clear. Process the request.
        kwargs_subset = kwargs.copy()
        
        for key in ['api_name', 'resource_name']:
            try:
                del(kwargs_subset[key])
            except KeyError:
                pass
        
        request = convert_post_to_put(request)
        response = method(request, **kwargs_subset)
        
        # Add the throttled request.
        self.throttle.accessed(self.authentication.get_identifier(request), url=request.get_full_path(), request_method=request_method)
        
        # If what comes back isn't a ``HttpResponse``, assume that the
        # request was accepted and that some action occurred. This also
        # prevents Django from freaking out.
        if not isinstance(response, HttpResponse):
            return HttpAccepted()
        
        return response
    
    def throttle_check(self, request):
        identifier = self.authentication.get_identifier(request)
        return self.throttle.should_be_throttled(identifier)
    
    def build_representation(self, data=None):
        if data is None:
            return self.representation(api_name=self.api_name, resource_name=self.resource_name)
        else:
            return self.representation(api_name=self.api_name, resource_name=self.resource_name, data=data)
    
    def fetch_list(self, **kwargs):
        return self.representation.get_list(options={
            'api_name': self.api_name,
            'resource_name': self.resource_name,
        })
    
    def cached_fetch_list(self, **kwargs):
        cache_key = self.generate_cache_key('list', **kwargs)
        representation_list = self.cache.get(cache_key)
        
        if representation_list is None:
            representation_list = self.fetch_list(**kwargs)
            self.cache.set(cache_key, representation_list)
        
        return representation_list
    
    def fetch_detail(self, **kwargs):
        """
        
        If not found, should raise a ``NotFound`` exception.
        """
        representation = self.build_representation()
        representation.get(pk=kwargs.get('obj_id'))
        return representation
    
    def cached_fetch_detail(self, **kwargs):
        cache_key = self.generate_cache_key('detail', **kwargs)
        representation = self.cache.get(cache_key)
        
        if representation is None:
            representation = self.fetch_detail(**kwargs)
            self.cache.set(cache_key, representation)
        
        return representation
    
    def generate_cache_key(self, *args, **kwargs):
        smooshed = []
        
        for key, value in kwargs.items():
            smooshed.append("%s=%s" % (key, value))
        
        # Use a list plus a ``.join()`` because it's faster than concatenation.
        return "%s:%s:%s:%s" % (self.api_name, self.resource_name, ':'.join(args), ':'.join(smooshed))
    
    def get_list(self, request, **kwargs):
        """
        Should return a HttpResponse (200 OK).
        """
        # TODO: Uncached for now. Invalidation that works for everyone may be
        #       impossible.
        objects = self.fetch_list(**kwargs)
        paginator = Paginator(request.GET, objects)
        
        try:
            object_list = paginator.page()
            desired_format = self.determine_format(request)
            serialized = self.serialize(request, object_list, desired_format)
        except BadRequest, e:
            return HttpBadRequest(e.args[0])
        
        return HttpResponse(content=serialized, content_type=build_content_type(desired_format))
    
    def get_detail(self, request, **kwargs):
        """
        Should return a HttpResponse (200 OK).
        """
        try:
            representation = self.cached_fetch_detail(**kwargs)
        except NotFound:
            return HttpGone()
        except MultipleRepresentationsFound:
            return HttpResponse("More than one resource is found at this URI.")
        
        desired_format = self.determine_format(request)
        
        try:
            serialized = self.serialize(request, representation.to_dict(), desired_format)
        except BadRequest, e:
            return HttpBadRequest(e.args[0])
        
        return HttpResponse(content=serialized, content_type=build_content_type(desired_format))
    
    def put_list(self, request, **kwargs):
        """
        Replaces a collection of resources with another collection.
        Return ``HttpAccepted`` (204 No Content).
        """
        deserialized = self.deserialize(request, request.raw_post_data, format=request.META.get('CONTENT_TYPE', 'application/json'))
        
        if not 'objects' in deserialized:
            return HttpBadRequest("Invalid data sent.")
        
        self.representation.delete_list(**kwargs)
        
        for object_data in deserialized['objects']:
            data = {}
            
            for key, value in object_data.items():
                data[str(key)] = value
            
            representation = self.build_representation(data=data)
            representation.create()
        
        return HttpAccepted()
    
    def put_detail(self, request, **kwargs):
        """
        If a new resource is created, return ``HttpCreated`` (201 Created).
        If an existing resource is modified, return ``HttpAccepted`` (204 No Content).
        """
        deserialized = self.deserialize(request, request.raw_post_data, format=request.META.get('CONTENT_TYPE', 'application/json'))
        data = {}
        
        for key, value in deserialized.items():
            data[str(key)] = value
        
        representation = self.build_representation(data=data)
        
        try:
            representation.update(pk=kwargs.get('obj_id'))
            return HttpAccepted()
        except:
            representation.create(pk=kwargs.get('obj_id'))
            return HttpCreated(location=representation.get_resource_uri())
    
    def post_list(self, request, **kwargs):
        """
        If a new resource is created, return ``HttpCreated`` (201 Created).
        """
        deserialized = self.deserialize(request, request.raw_post_data, format=request.META.get('CONTENT_TYPE', 'application/json'))
        data = {}
        
        for key, value in deserialized.items():
            data[str(key)] = value
        
        representation = self.build_representation(data=data)
        representation.create()
        return HttpCreated(location=representation.get_resource_uri())
    
    def post_detail(self, request, **kwargs):
        """
        This is not implemented by default because most people's data models
        aren't self-referential.
        
        If a new resource is created, return ``HttpCreated`` (201 Created).
        """
        return HttpNotImplemented()
    
    def delete_list(self, request, **kwargs):
        """
        If the resources are deleted, return ``HttpAccepted`` (204 No Content).
        """
        self.representation.delete_list(**kwargs)
        return HttpAccepted()
    
    def delete_detail(self, request, **kwargs):
        """
        If the resource is deleted, return ``HttpAccepted`` (204 No Content).
        """
        representation = self.fetch_detail(**kwargs)
        
        try:
            representation.delete()
            return HttpAccepted()
        except:
            return HttpGone()
    
    def get_schema(self, request, **kwargs):
        """
        Should return a HttpResponse (200 OK).
        """
        request_method = request.method.lower()
        
        if request_method != 'get':
            return HttpMethodNotAllowed()
        
        auth_result = self.authentication.is_authenticated(request)
        
        if isinstance(auth_result, HttpResponse):
            return auth_result
        
        if not auth_result is True:
            return HttpUnauthorized()
        
        # Check to see if they should be throttled.
        if self.throttle_check(request):
            # Throttle limit exceeded.
            return HttpForbidden()
        
        representation = self.build_representation()
        desired_format = self.determine_format(request)
        
        # Add the throttled request.
        self.throttle.accessed(self.authentication.get_identifier(request), url=request.get_full_path(), request_method=request_method)
        
        try:
            serialized = self.serialize(request, representation.build_schema(), desired_format)
        except BadRequest, e:
            return HttpBadRequest(e.args[0])
        
        return HttpResponse(content=serialized, content_type=build_content_type(desired_format))
    
    def get_multiple(self, request, **kwargs):
        """
        Should return a HttpResponse (200 OK).
        """
        request_method = request.method.lower()
        
        if request_method != 'get':
            return HttpMethodNotAllowed()
        
        auth_result = self.authentication.is_authenticated(request)
        
        if isinstance(auth_result, HttpResponse):
            return auth_result
        
        if not auth_result is True:
            return HttpForbidden()
        
        # Check to see if they should be throttled.
        if self.throttle_check(request):
            # Throttle limit exceeded.
            return HttpBadRequest()
        
        # Rip apart the list then iterate.
        repr_ids = kwargs.get('id_list', '').split(';')
        objects = []
        not_found = []
        
        for obj_id in repr_ids:
            try:
                representation = self.fetch_detail(obj_id=obj_id)
                objects.append(representation)
            except NotFound:
                not_found.append(obj_id)
        
        object_list = {
            'objects': objects,
        }
        
        if len(not_found):
            object_list['not_found'] = not_found
        
        # Add the throttled request.
        self.throttle.accessed(self.authentication.get_identifier(request), url=request.get_full_path(), request_method=request_method)
        desired_format = self.determine_format(request)
        
        try:
            serialized = self.serialize(request, object_list, desired_format)
        except BadRequest, e:
            return HttpBadRequest(e.args[0])
        
        return HttpResponse(content=serialized, content_type=build_content_type(desired_format))


# Based off of ``piston.utils.coerce_put_post``. Similarly BSD-licensed.
# And no, the irony is not lost on me.
def convert_post_to_put(request):
    """
    Force Django to process the PUT.
    """
    if request.method == "PUT":
        if hasattr(request, '_post'):
            del request._post
            del request._files
        
        try:
            request.method = "POST"
            request._load_post_and_files()
            request.method = "PUT"
        except AttributeError:
            request.META['REQUEST_METHOD'] = 'POST'
            request._load_post_and_files()
            request.META['REQUEST_METHOD'] = 'PUT'
            
        request.PUT = request.POST
    
    return request
