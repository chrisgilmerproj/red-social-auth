import json, logging, re, urllib

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect
from django.views.decorators.cache import never_cache

import tweepy

from social_auth.forms  import IdentityProviderForm
from social_auth.models import SocialUser

FACEBOOK_API_KEY    = getattr(settings, 'FACEBOOK_API_KEY', None)
FACEBOOK_API_SECRET = getattr(settings, 'FACEBOOK_API_SECRET', None)
TWITTER_API_KEY     = getattr(settings, 'TWITTER_API_KEY', None)
TWITTER_API_SECRET  = getattr(settings, 'TWITTER_API_SECRET', None)

def logout(request):
	redirect_url = '/'
	if 'next' in request.GET:
		redirect_url = request.GET['next']
		request.session['next'] = redirect_url
	elif 'next' in request.session:
		redirect_url = request.session['next']

	request.session.flush()
	return HttpResponseRedirect(redirect_url)

@never_cache
def status(request):
	user = request.session.get('user',None)
	obj = None
	if user and user.has_valid_session():
		obj = {
				'pk'        : user.id,
				'username'  : user.username,
				'image_url' : user.image_url,
				'created'   : user.created.strftime('%Y-%m-%d %H-%M-%S'),
				'banned'    : user.banned,
				'identities': {
						'twitter':  hasattr(user,'twitter')  and user.twitter  or None,
						'facebook': hasattr(user,'facebook') and user.facebook or None,
					},
				}

	return HttpResponse(json.dumps({'user':obj}),mimetype="application/json")

def submit(request):
	if request.POST:
		form = IdentityProviderForm(request)
		if form.is_valid():
			provider = form.cleaned_data['provider']
			user_info     = {
				'token'            : form.cleaned_data['token'],
				'external_user_id' : form.cleaned_data['external_user_id'],
				'name'             : form.cleaned_data['name'],
				'image_url'        : form.cleaned_data['image_url'],
				'data'             : form.cleaned_data['data'],
				}
			user = None
			if 'user' in request.session:
				user = request.session['user']
			request.session['user'] = SocialUser.lookup(provider, user, user_info)
			return redirect('auth_status')
	
	return HttpResponse(json.dumps({'error':'post request invalid'}),mimetype="application/json")

def _get_access_token(request, provider):
	if 'user' in request.session:
		user = request.session.get('user')
		identity = user.get_identity(provider)
		if identity:
			return json.loads(identity.token)
	
	if '%s_access_token' % provider in request.session:
		return request.session.get('%s_access_token' % provider)
	
	return redirect('auth_%s' % provider)

# Facebook

def call_facebook_api(request, method=None, **kwargs):
	graph_dict = {'access_token' : _get_access_token(request, 'facebook')}
	graph_dict.update(kwargs)
	data = urllib.urlencode(graph_dict)
	url  = 'https://graph.facebook.com/%s' % method
	if method !='me': 
		response = json.loads(urllib.urlopen(url, data).read())
	else:
		url += '?%s' % data
		response = json.loads(urllib.urlopen(url).read())
	return response

def facebook(request):
	
	redirect_url = '/'
	if 'next' in request.GET:
		redirect_url = request.GET['next']
		request.session['next'] = redirect_url
	elif 'next' in request.session:
		redirect_url = request.session['next']

	access_url    = "https://graph.facebook.com/oauth/access_token"
	authorize_url = "https://graph.facebook.com/oauth/authorize"
	callback_url  = request.build_absolute_uri()
	values        = {
		'client_id'    : FACEBOOK_API_KEY,
  		'redirect_uri' : 'http://%s%s' % (request.get_host(), request.path),
		'scope'        : 'publish_stream'
		}
	
	if 'user' in request.session:
		user     = request.session['user']
		if user.has_valid_session():
			return HttpResponseRedirect(redirect_url)
    
	# TODO: Add a way to manage error responses
	# error_reason=user_denied&error=access_denied&error_description=The+user+denied+your+request
	if 'error' in request.GET:
		logging.warning(request, 'Could not authorize on Facebook!')
		return HttpResponseRedirect(redirect_url)

	if 'code' in request.GET:
		values['code']          = request.GET.get('code')
		values['client_secret'] = FACEBOOK_API_SECRET
		facebook_url = "%s?%s" % (access_url, urllib.urlencode(values))
		result       = urllib.urlopen(facebook_url).read()
		access_token = re.findall('^access_token=([^&]*)', result)[0]
		expires      = result.split('expires=')[1]
		request.session['facebook_access_token'] = access_token
		
		facebook_user = call_facebook_api(request, 'me', **{'fields':'id,name,picture'})
		user_info     = {
			'token'            : json.dumps(request.session['facebook_access_token']),
			'external_user_id' : facebook_user['id'],
			'name'             : facebook_user['name'],
			'image_url'        : facebook_user['picture'],
			'expires'          : expires,
			'data'             : facebook_user,
			}

		user = request.session.get('user',None)
		s_user = SocialUser.lookup('facebook', user, user_info)
		s_user.facebook = {
					'name'             : user_info['name'],
					'image_url'        : user_info['image_url'],
					'external_user_id' : user_info['external_user_id'],
				}
		request.session['user'] = s_user
		return HttpResponseRedirect(redirect_url) 
	redirect_url  = "%s?%s" % (authorize_url, urllib.urlencode(values))
	return HttpResponseRedirect(redirect_url)

# Twitter

def get_twitter_api(request):
	access_token = _get_access_token(request,'twitter')
	auth = tweepy.OAuthHandler(TWITTER_API_KEY, TWITTER_API_SECRET)
	auth.set_access_token(access_token[0], access_token[1])
	return tweepy.API(auth)

def twitter(request):

	redirect_url = '/'
	if 'next' in request.GET:
		redirect_url = request.GET['next']
		request.session['next'] = redirect_url
	elif 'next' in request.session:
		redirect_url = request.session['next']

	if 'user' in request.session:
		user = request.session['user']
		identity = user.get_identity('twitter')
		if identity: return HttpResponseRedirect(redirect_url)

	if 'oauth_verifier' in request.GET:
		auth = tweepy.OAuthHandler(TWITTER_API_KEY, TWITTER_API_SECRET)
		token = request.session.get('twitter_request_token')
		if 'twitter_request_token' in request.session:
			del request.session['twitter_request_token']
		auth.set_request_token(token[0], token[1])
		try:
			access_token = auth.get_access_token(request.GET.get('oauth_verifier'))
			# And now let's store it in the session!
			request.session['twitter_access_token'] = (access_token.key, access_token.secret)
			
			twitter_user = get_twitter_api(request).me()
			user_info = {
				'token'            : json.dumps(request.session['twitter_access_token']),
				'external_user_id' : twitter_user.id,
				'name'             : twitter_user.screen_name,
				'image_url'        : twitter_user.profile_image_url,
				'data'             : twitter_user.__dict__,
				}
			
			user = None
			if 'user' in request.session:
				user = request.session['user']
			s_user = SocialUser.lookup('twitter', user, user_info)
			s_user.twitter = {
						'name'             : user_info['name'],
						'image_url'        : user_info['image_url'],
						'external_user_id' : user_info['external_user_id'],
					}
			request.session['user'] = s_user

		except tweepy.TweepError:
			logging.warning('Error! Failed to get twitter request token.')
			
		return HttpResponseRedirect(redirect_url) 
	
	# Authenticate with Twitter and get redirect_url
	callback_url = request.build_absolute_uri()
	auth = tweepy.OAuthHandler(TWITTER_API_KEY, TWITTER_API_SECRET, callback_url)
	try:
		redirect_url = auth.get_authorization_url()
	except tweepy.TweepError:
		logging.warning('Error! Failed to get twitter request token.')

	# Store the request token in the session
	request.session['twitter_request_token'] = (auth.request_token.key, auth.request_token.secret)
	return HttpResponseRedirect(redirect_url)

