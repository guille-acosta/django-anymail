from datetime import datetime
from email.mime.text import MIMEText

import six
from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import get_connection, send_mail
from django.test import SimpleTestCase
from django.test.utils import override_settings
from django.utils.functional import Promise
from django.utils.timezone import utc
from django.utils.translation import ugettext_lazy

from anymail.backends.test import EmailBackend as TestBackend, TestPayload
from anymail.exceptions import AnymailConfigurationError, AnymailInvalidAddress, AnymailUnsupportedFeature
from anymail.message import AnymailMessage
from anymail.utils import get_anymail_setting

from .utils import AnymailTestMixin


class SettingsTestBackend(TestBackend):
    """(useful only for these tests)"""
    def __init__(self, *args, **kwargs):
        esp_name = self.esp_name
        self.sample_setting = get_anymail_setting('sample_setting', esp_name=esp_name,
                                                  kwargs=kwargs, allow_bare=True)
        self.username = get_anymail_setting('username', esp_name=esp_name, kwargs=kwargs,
                                            default=None, allow_bare=True)
        self.password = get_anymail_setting('password', esp_name=esp_name, kwargs=kwargs,
                                            default=None, allow_bare=True)
        super(SettingsTestBackend, self).__init__(*args, **kwargs)


@override_settings(EMAIL_BACKEND='anymail.backends.test.EmailBackend')
class TestBackendTestCase(SimpleTestCase, AnymailTestMixin):
    """Base TestCase using Anymail's Test EmailBackend"""

    def setUp(self):
        super(TestBackendTestCase, self).setUp()
        # Simple message useful for many tests
        self.message = AnymailMessage('Subject', 'Text Body', 'from@example.com', ['to@example.com'])

    @staticmethod
    def get_send_count():
        """Returns number of times "send api" has been called this test"""
        try:
            return len(mail.outbox)
        except AttributeError:
            return 0  # mail.outbox not initialized by either Anymail test or Django locmem backend

    @staticmethod
    def get_send_params():
        """Returns the params for the most recent "send api" call"""
        try:
            return mail.outbox[-1].anymail_test_params
        except IndexError:
            raise IndexError("No messages have been sent through the Anymail test backend")
        except AttributeError:
            raise AttributeError("The last message sent was not processed through the Anymail test backend")


@override_settings(EMAIL_BACKEND='tests.test_general_backend.SettingsTestBackend')
class BackendSettingsTests(TestBackendTestCase):
    """Test settings initializations for Anymail EmailBackends"""

    @override_settings(ANYMAIL={'TEST_SAMPLE_SETTING': 'setting_from_anymail_settings'})
    def test_anymail_setting(self):
        """ESP settings usually come from ANYMAIL settings dict"""
        backend = get_connection()
        self.assertEqual(backend.sample_setting, 'setting_from_anymail_settings')

    @override_settings(TEST_SAMPLE_SETTING='setting_from_bare_settings')
    def test_bare_setting(self):
        """ESP settings are also usually allowed at root of settings file"""
        backend = get_connection()
        self.assertEqual(backend.sample_setting, 'setting_from_bare_settings')

    @override_settings(ANYMAIL={'TEST_SAMPLE_SETTING': 'setting_from_settings'})
    def test_connection_kwargs_overrides_settings(self):
        """Can override settings file in get_connection"""
        backend = get_connection()
        self.assertEqual(backend.sample_setting, 'setting_from_settings')

        backend = get_connection(sample_setting='setting_from_kwargs')
        self.assertEqual(backend.sample_setting, 'setting_from_kwargs')

    def test_missing_setting(self):
        """Settings without defaults must be provided"""
        with self.assertRaises(AnymailConfigurationError) as cm:
            get_connection()
        self.assertIsInstance(cm.exception, ImproperlyConfigured)  # Django consistency
        errmsg = str(cm.exception)
        self.assertRegex(errmsg, r'\bTEST_SAMPLE_SETTING\b')
        self.assertRegex(errmsg, r'\bANYMAIL_TEST_SAMPLE_SETTING\b')

    @override_settings(ANYMAIL={'TEST_USERNAME': 'username_from_settings',
                                'TEST_PASSWORD': 'password_from_settings',
                                'TEST_SAMPLE_SETTING': 'required'})
    def test_username_password_kwargs_overrides(self):
        """Overrides for 'username' and 'password' should work like other overrides"""
        # These are special-cased because of default args in Django core mail functions.
        backend = get_connection()
        self.assertEqual(backend.username, 'username_from_settings')
        self.assertEqual(backend.password, 'password_from_settings')

        backend = get_connection(username='username_from_kwargs', password='password_from_kwargs')
        self.assertEqual(backend.username, 'username_from_kwargs')
        self.assertEqual(backend.password, 'password_from_kwargs')


class UnsupportedFeatureTests(TestBackendTestCase):
    """Tests mail features not supported by backend are handled properly"""

    def test_unsupported_feature(self):
        """Unsupported features raise AnymailUnsupportedFeature"""
        # Test EmailBackend doesn't support non-HTML alternative parts
        self.message.attach_alternative(b'FAKE_MP3_DATA', 'audio/mpeg')
        with self.assertRaises(AnymailUnsupportedFeature):
            self.message.send()

    @override_settings(ANYMAIL={
        'IGNORE_UNSUPPORTED_FEATURES': True
    })
    def test_ignore_unsupported_features(self):
        """Setting prevents exception"""
        self.message.attach_alternative(b'FAKE_MP3_DATA', 'audio/mpeg')
        self.message.send()  # should not raise exception


class SendDefaultsTests(TestBackendTestCase):
    """Tests backend support for global SEND_DEFAULTS and <ESP>_SEND_DEFAULTS"""

    @override_settings(ANYMAIL={
        'SEND_DEFAULTS': {
            # This isn't an exhaustive list of Anymail message attrs; just one of each type
            'metadata': {'global': 'globalvalue'},
            'send_at': datetime(2016, 5, 12, 4, 17, 0, tzinfo=utc),
            'tags': ['globaltag'],
            'template_id': 'my-template',
            'track_clicks': True,
            'esp_extra': {'globalextra': 'globalsetting'},
        }
    })
    def test_send_defaults(self):
        """Test that (non-esp-specific) send defaults are applied"""
        self.message.send()
        params = self.get_send_params()
        # All these values came from ANYMAIL_SEND_DEFAULTS:
        self.assertEqual(params['metadata'], {'global': 'globalvalue'})
        self.assertEqual(params['send_at'], datetime(2016, 5, 12, 4, 17, 0, tzinfo=utc))
        self.assertEqual(params['tags'], ['globaltag'])
        self.assertEqual(params['template_id'], 'my-template')
        self.assertEqual(params['track_clicks'], True)
        self.assertEqual(params['globalextra'], 'globalsetting')  # Test EmailBackend merges esp_extra into params

    @override_settings(ANYMAIL={
        'TEST_SEND_DEFAULTS': {  # "TEST" is the name of the Test EmailBackend's ESP
            'metadata': {'global': 'espvalue'},
            'tags': ['esptag'],
            'track_opens': False,
            'esp_extra': {'globalextra': 'espsetting'},
        }
    })
    def test_esp_send_defaults(self):
        """Test that esp-specific send defaults are applied"""
        self.message.send()
        params = self.get_send_params()
        self.assertEqual(params['metadata'], {'global': 'espvalue'})
        self.assertEqual(params['tags'], ['esptag'])
        self.assertEqual(params['track_opens'], False)
        self.assertEqual(params['globalextra'], 'espsetting')  # Test EmailBackend merges esp_extra into params

    @override_settings(ANYMAIL={
        'SEND_DEFAULTS': {
            'metadata': {'global': 'globalvalue', 'other': 'othervalue'},
            'tags': ['globaltag'],
            'track_clicks': True,
            'track_opens': False,
            'esp_extra': {'globalextra': 'globalsetting'},
        }
    })
    def test_send_defaults_combine_with_message(self):
        """Individual message settings are *merged into* the global send defaults"""
        self.message.metadata = {'message': 'messagevalue', 'other': 'override'}
        self.message.tags = ['messagetag']
        self.message.track_clicks = False
        self.message.esp_extra = {'messageextra': 'messagesetting'}

        self.message.send()
        params = self.get_send_params()
        self.assertEqual(params['metadata'], {  # metadata merged
            'global': 'globalvalue',  # global default preserved
            'message': 'messagevalue',  # message setting added
            'other': 'override'})  # message setting overrides global default
        self.assertEqual(params['tags'], ['globaltag', 'messagetag'])  # tags concatenated
        self.assertEqual(params['track_clicks'], False)  # message overrides
        self.assertEqual(params['track_opens'], False)  # (no message setting)
        self.assertEqual(params['globalextra'], 'globalsetting')
        self.assertEqual(params['messageextra'], 'messagesetting')

        # Send another message to make sure original SEND_DEFAULTS unchanged
        send_mail('subject', 'body', 'from@example.com', ['to@example.com'])
        params = self.get_send_params()
        self.assertEqual(params['metadata'], {'global': 'globalvalue', 'other': 'othervalue'})
        self.assertEqual(params['tags'], ['globaltag'])
        self.assertEqual(params['track_clicks'], True)
        self.assertEqual(params['track_opens'], False)
        self.assertEqual(params['globalextra'], 'globalsetting')

    @override_settings(ANYMAIL={
        'SEND_DEFAULTS': {
            # This isn't an exhaustive list of Anymail message attrs; just one of each type
            'metadata': {'global': 'globalvalue'},
            'tags': ['globaltag'],
            'template_id': 'global-template',
            'esp_extra': {'globalextra': 'globalsetting'},
        },
        'TEST_SEND_DEFAULTS': {  # "TEST" is the name of the Test EmailBackend's ESP
            'merge_global_data': {'esp': 'espmerge'},
            'metadata': {'esp': 'espvalue'},
            'tags': ['esptag'],
            'esp_extra': {'espextra': 'espsetting'},
        }
    })
    def test_esp_send_defaults_override_globals(self):
        """ESP-specific send defaults override *individual* global defaults"""
        self.message.send()
        params = self.get_send_params()
        self.assertEqual(params['merge_global_data'], {'esp': 'espmerge'})  # esp-defaults only
        self.assertEqual(params['metadata'], {'esp': 'espvalue'})
        self.assertEqual(params['tags'], ['esptag'])
        self.assertEqual(params['template_id'], 'global-template')  # global-defaults only
        self.assertEqual(params['espextra'], 'espsetting')
        self.assertNotIn('globalextra', params)  # entire esp_extra is overriden by esp-send-defaults


class LazyStringsTest(TestBackendTestCase):
    """
    Tests ugettext_lazy strings forced real before passing to ESP transport.

    Docs notwithstanding, Django lazy strings *don't* work anywhere regular
    strings would. In particular, they aren't instances of unicode/str.
    There are some cases (e.g., urllib.urlencode, requests' _encode_params)
    where this can cause encoding errors or just very wrong results.

    Since Anymail sits on the border between Django app code and non-Django
    ESP code (e.g., requests), it's responsible for converting lazy text
    to actual strings.
    """

    def assertNotLazy(self, s, msg=None):
        self.assertNotIsInstance(s, Promise,
                                 msg=msg or "String %r is lazy" % six.text_type(s))

    def test_lazy_from(self):
        # This sometimes ends up lazy when settings.DEFAULT_FROM_EMAIL is meant to be localized
        self.message.from_email = ugettext_lazy('"Global Sales" <sales@example.com>')
        self.message.send()
        params = self.get_send_params()
        self.assertNotLazy(params['from'].address)

    def test_lazy_subject(self):
        self.message.subject = ugettext_lazy("subject")
        self.message.send()
        params = self.get_send_params()
        self.assertNotLazy(params['subject'])

    def test_lazy_body(self):
        self.message.body = ugettext_lazy("text body")
        self.message.attach_alternative(ugettext_lazy("html body"), "text/html")
        self.message.send()
        params = self.get_send_params()
        self.assertNotLazy(params['text_body'])
        self.assertNotLazy(params['html_body'])

    def test_lazy_headers(self):
        self.message.extra_headers['X-Test'] = ugettext_lazy("Test Header")
        self.message.send()
        params = self.get_send_params()
        self.assertNotLazy(params['extra_headers']['X-Test'])

    def test_lazy_attachments(self):
        self.message.attach(ugettext_lazy("test.csv"), ugettext_lazy("test,csv,data"), "text/csv")
        self.message.attach(MIMEText(ugettext_lazy("contact info")))
        self.message.send()
        params = self.get_send_params()
        self.assertNotLazy(params['attachments'][0].name)
        self.assertNotLazy(params['attachments'][0].content)
        self.assertNotLazy(params['attachments'][1].content)

    def test_lazy_tags(self):
        self.message.tags = [ugettext_lazy("Shipping"), ugettext_lazy("Sales")]
        self.message.send()
        params = self.get_send_params()
        self.assertNotLazy(params['tags'][0])
        self.assertNotLazy(params['tags'][1])

    def test_lazy_metadata(self):
        self.message.metadata = {'order_type': ugettext_lazy("Subscription")}
        self.message.send()
        params = self.get_send_params()
        self.assertNotLazy(params['metadata']['order_type'])

    def test_lazy_merge_data(self):
        self.message.merge_data = {
            'to@example.com': {'duration': ugettext_lazy("One Month")}}
        self.message.merge_global_data = {'order_type': ugettext_lazy("Subscription")}
        self.message.send()
        params = self.get_send_params()
        self.assertNotLazy(params['merge_data']['to@example.com']['duration'])
        self.assertNotLazy(params['merge_global_data']['order_type'])


class CatchCommonErrorsTests(TestBackendTestCase):
    """Anymail should catch and provide useful errors for common mistakes"""

    def test_explains_reply_to_must_be_list(self):
        """reply_to must be a list (or other iterable), not a single string"""
        # Django's EmailMessage.__init__ catches this and warns, but isn't
        # involved if you assign attributes later. Anymail should catch that case.
        # (This also applies to to, cc, and bcc, but Django stumbles over those cases
        # in EmailMessage.recipients (called from EmailMessage.send) before
        # Anymail gets a chance to complain.)
        self.message.reply_to = "single-reply-to@example.com"
        with self.assertRaisesMessage(TypeError, '"reply_to" attribute must be a list or other iterable'):
            self.message.send()

    def test_explains_reply_to_must_be_list_lazy(self):
        """Same as previous tests, with lazy strings"""
        # Lazy strings can fool string/iterable detection
        self.message.reply_to = ugettext_lazy("single-reply-to@example.com")
        with self.assertRaisesMessage(TypeError, '"reply_to" attribute must be a list or other iterable'):
            self.message.send()

    def test_identifies_source_of_parsing_errors(self):
        """Errors parsing email addresses should say which field had the problem"""
        # Note: General email address parsing tests are in test_utils.ParseAddressListTests.
        # This just checks the error includes the field name when parsing for sending a message.
        self.message.from_email = ''
        with self.assertRaisesMessage(AnymailInvalidAddress,
                                      "Invalid email address '' parsed from '' in `from_email`."):
            self.message.send()
        self.message.from_email = 'from@example.com'

        # parse_address_list
        self.message.to = ['ok@example.com', 'oops']
        with self.assertRaisesMessage(AnymailInvalidAddress,
                                      "Invalid email address 'oops' parsed from 'ok@example.com, oops' in `to`."):
            self.message.send()
        self.message.to = ['test@example.com']

        # parse_single_address
        self.message.envelope_sender = 'one@example.com, two@example.com'
        with self.assertRaisesMessage(AnymailInvalidAddress,
                                      "Only one email address is allowed; found 2"
                                      " in 'one@example.com, two@example.com' in `envelope_sender`."):
            self.message.send()
        delattr(self.message, 'envelope_sender')

        # process_extra_headers
        self.message.extra_headers['From'] = 'Mail, Inc. <mail@example.com>'
        with self.assertRaisesMessage(AnymailInvalidAddress,
                                      "Invalid email address 'Mail' parsed from 'Mail, Inc. <mail@example.com>'"
                                      " in `extra_headers['From']`. (Maybe missing quotes around a display-name?)"):
            self.message.send()


def flatten_emails(emails):
    return [str(email) for email in emails]


class SpecialHeaderTests(TestBackendTestCase):
    """Anymail should handle special extra_headers the same way Django does"""

    def test_reply_to(self):
        """Django allows message.reply_to and message.extra_headers['Reply-To'], and the latter takes precedence"""
        self.message.reply_to = ["attr@example.com"]
        self.message.extra_headers = {"X-Extra": "extra"}
        self.message.send()
        params = self.get_send_params()
        self.assertEqual(flatten_emails(params['reply_to']), ["attr@example.com"])
        self.assertEqual(params['extra_headers'], {"X-Extra": "extra"})

        self.message.reply_to = None
        self.message.extra_headers = {"Reply-To": "header@example.com", "X-Extra": "extra"}
        self.message.send()
        params = self.get_send_params()
        self.assertEqual(flatten_emails(params['reply_to']), ["header@example.com"])
        self.assertEqual(params['extra_headers'], {"X-Extra": "extra"})  # Reply-To no longer there

        # If both are supplied, the header wins (to match Django EmailMessage.message() behavior).
        # Also, header names are case-insensitive.
        self.message.reply_to = ["attr@example.com"]
        self.message.extra_headers = {"REPLY-to": "header@example.com", "X-Extra": "extra"}
        self.message.send()
        params = self.get_send_params()
        self.assertEqual(flatten_emails(params['reply_to']), ["header@example.com"])
        self.assertEqual(params['extra_headers'], {"X-Extra": "extra"})  # Reply-To no longer there

    def test_envelope_sender(self):
        """Django treats message.from_email as envelope-sender if messsage.extra_headers['From'] is set"""
        # Using Anymail's envelope_sender extension
        self.message.from_email = "Header From <header@example.com>"
        self.message.envelope_sender = "Envelope From <envelope@bounces.example.com>"  # Anymail extension
        self.message.send()
        params = self.get_send_params()
        self.assertEqual(params['from'].address, "Header From <header@example.com>")
        self.assertEqual(params['envelope_sender'], "envelope@bounces.example.com")

        # Using Django's undocumented message.extra_headers['From'] extension
        # (see https://code.djangoproject.com/ticket/9214)
        self.message.from_email = "Envelope From <envelope@bounces.example.com>"
        self.message.extra_headers = {"From": "Header From <header@example.com>"}
        self.message.send()
        params = self.get_send_params()
        self.assertEqual(params['from'].address, "Header From <header@example.com>")
        self.assertEqual(params['envelope_sender'], "envelope@bounces.example.com")
        self.assertNotIn("From", params.get('extra_headers', {}))  # From was removed from extra-headers

    def test_spoofed_to_header(self):
        """Django treats message.to as envelope-recipient if message.extra_headers['To'] is set"""
        # No current ESP supports this (and it's unlikely they would)
        self.message.to = ["actual-recipient@example.com"]
        self.message.extra_headers = {"To": "Apparent Recipient <but-not-really@example.com>"}
        with self.assertRaisesMessage(AnymailUnsupportedFeature, "spoofing `To` header"):
            self.message.send()


class BatchSendDetectionTestCase(TestBackendTestCase):
    """Tests shared code to consistently determine whether to use batch send"""

    def setUp(self):
        super(BatchSendDetectionTestCase, self).setUp()
        self.backend = TestBackend()

    def test_default_is_not_batch(self):
        payload = self.backend.build_message_payload(self.message, {})
        self.assertFalse(payload.is_batch())

    def test_merge_data_implies_batch(self):
        self.message.merge_data = {}  # *anything* (even empty dict) implies batch
        payload = self.backend.build_message_payload(self.message, {})
        self.assertTrue(payload.is_batch())

    def test_merge_metadata_implies_batch(self):
        self.message.merge_metadata = {}  # *anything* (even empty dict) implies batch
        payload = self.backend.build_message_payload(self.message, {})
        self.assertTrue(payload.is_batch())

    def test_merge_global_data_does_not_imply_batch(self):
        self.message.merge_global_data = {}
        payload = self.backend.build_message_payload(self.message, {})
        self.assertFalse(payload.is_batch())

    def test_cannot_call_is_batch_during_init(self):
        # It's tempting to try to warn about unsupported batch features in setters,
        # but because of the way payload attrs are processed, it won't work...
        class ImproperlyImplementedPayload(TestPayload):
            def set_cc(self, emails):
                if self.is_batch():  # this won't work here!
                    self.unsupported_feature("cc with batch send")
                super(ImproperlyImplementedPayload, self).set_cc(emails)

        connection = mail.get_connection('anymail.backends.test.EmailBackend',
                                         payload_class=ImproperlyImplementedPayload)
        with self.assertRaisesMessage(AssertionError,
                                      "Cannot call is_batch before all attributes processed"):
            connection.send_messages([self.message])
