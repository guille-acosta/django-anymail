# Tests for the anymail/utils.py module
# (not to be confused with utilities for testing found in in tests/utils.py)
import base64
import copy
import pickle
from email.mime.image import MIMEImage

from django.http import QueryDict
from django.test import SimpleTestCase, RequestFactory, override_settings
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy

from anymail.exceptions import AnymailInvalidAddress, _LazyError
from anymail.utils import (
    parse_address_list, parse_single_address, EmailAddress,
    Attachment,
    is_lazy, force_non_lazy, force_non_lazy_dict, force_non_lazy_list,
    update_deep, UNSET,
    get_request_uri, get_request_basic_auth, parse_rfc2822date, querydict_getfirst,
    CaseInsensitiveCasePreservingDict)


class ParseAddressListTests(SimpleTestCase):
    """Test utils.parse_address_list"""

    def test_simple_email(self):
        parsed_list = parse_address_list(["test@example.com"])
        self.assertEqual(len(parsed_list), 1)
        parsed = parsed_list[0]
        self.assertIsInstance(parsed, EmailAddress)
        self.assertEqual(parsed.addr_spec, "test@example.com")
        self.assertEqual(parsed.display_name, "")
        self.assertEqual(parsed.address, "test@example.com")
        self.assertEqual(parsed.username, "test")
        self.assertEqual(parsed.domain, "example.com")

    def test_display_name(self):
        parsed_list = parse_address_list(['"Display Name, Inc." <test@example.com>'])
        self.assertEqual(len(parsed_list), 1)
        parsed = parsed_list[0]
        self.assertEqual(parsed.addr_spec, "test@example.com")
        self.assertEqual(parsed.display_name, "Display Name, Inc.")
        self.assertEqual(parsed.address, '"Display Name, Inc." <test@example.com>')
        self.assertEqual(parsed.username, "test")
        self.assertEqual(parsed.domain, "example.com")

    def test_obsolete_display_name(self):
        # you can get away without the quotes if there are no commas or parens
        # (but it's not recommended)
        parsed_list = parse_address_list(['Display Name <test@example.com>'])
        self.assertEqual(len(parsed_list), 1)
        parsed = parsed_list[0]
        self.assertEqual(parsed.addr_spec, "test@example.com")
        self.assertEqual(parsed.display_name, "Display Name")
        self.assertEqual(parsed.address, 'Display Name <test@example.com>')

    def test_unicode_display_name(self):
        parsed_list = parse_address_list(['"Unicode \N{HEAVY BLACK HEART}" <test@example.com>'])
        self.assertEqual(len(parsed_list), 1)
        parsed = parsed_list[0]
        self.assertEqual(parsed.addr_spec, "test@example.com")
        self.assertEqual(parsed.display_name, "Unicode \N{HEAVY BLACK HEART}")
        # formatted display-name automatically shifts to quoted-printable/base64 for non-ascii chars:
        self.assertEqual(parsed.address, '=?utf-8?b?VW5pY29kZSDinaQ=?= <test@example.com>')

    def test_invalid_display_name(self):
        with self.assertRaisesMessage(AnymailInvalidAddress, "Invalid email address 'webmaster'"):
            parse_address_list(['webmaster'])

        with self.assertRaisesMessage(AnymailInvalidAddress, "Maybe missing quotes around a display-name?"):
            # this parses as multiple email addresses, because of the comma:
            parse_address_list(['Display Name, Inc. <test@example.com>'])

    def test_idn(self):
        parsed_list = parse_address_list(["idn@\N{ENVELOPE}.example.com"])
        self.assertEqual(len(parsed_list), 1)
        parsed = parsed_list[0]
        self.assertEqual(parsed.addr_spec, "idn@\N{ENVELOPE}.example.com")
        self.assertEqual(parsed.address, "idn@xn--4bi.example.com")  # punycode-encoded domain
        self.assertEqual(parsed.username, "idn")
        self.assertEqual(parsed.domain, "\N{ENVELOPE}.example.com")

    def test_none_address(self):
        # used for, e.g., telling Mandrill to use template default from_email
        self.assertEqual(parse_address_list([None]), [])
        self.assertEqual(parse_address_list(None), [])

    def test_empty_address(self):
        with self.assertRaises(AnymailInvalidAddress):
            parse_address_list([''])

    def test_whitespace_only_address(self):
        with self.assertRaises(AnymailInvalidAddress):
            parse_address_list([' '])

    def test_invalid_address(self):
        with self.assertRaises(AnymailInvalidAddress):
            parse_address_list(['localonly'])
        with self.assertRaises(AnymailInvalidAddress):
            parse_address_list(['localonly@'])
        with self.assertRaises(AnymailInvalidAddress):
            parse_address_list(['@domainonly'])
        with self.assertRaises(AnymailInvalidAddress):
            parse_address_list(['<localonly@>'])
        with self.assertRaises(AnymailInvalidAddress):
            parse_address_list(['<@domainonly>'])

    def test_email_list(self):
        parsed_list = parse_address_list(["first@example.com", "second@example.com"])
        self.assertEqual(len(parsed_list), 2)
        self.assertEqual(parsed_list[0].addr_spec, "first@example.com")
        self.assertEqual(parsed_list[1].addr_spec, "second@example.com")

    def test_multiple_emails(self):
        # Django's EmailMessage allows multiple, comma-separated emails
        # in a single recipient string. (It passes them along to the backend intact.)
        # (Depending on this behavior is not recommended.)
        parsed_list = parse_address_list(["first@example.com, second@example.com"])
        self.assertEqual(len(parsed_list), 2)
        self.assertEqual(parsed_list[0].addr_spec, "first@example.com")
        self.assertEqual(parsed_list[1].addr_spec, "second@example.com")

    def test_invalid_in_list(self):
        # Make sure it's not just concatenating list items...
        # the bare "Display Name" below should *not* get merged with
        # the email in the second item
        with self.assertRaisesMessage(AnymailInvalidAddress, "Display Name"):
            parse_address_list(['"Display Name"', '<valid@example.com>'])

    def test_invalid_with_unicode(self):
        # (assertRaisesMessage can't handle unicode in Python 2)
        with self.assertRaises(AnymailInvalidAddress) as cm:
            parse_address_list(["\N{ENVELOPE}"])
        self.assertIn("Invalid email address '\N{ENVELOPE}'", str(cm.exception))

    def test_single_string(self):
        # bare strings are used by the from_email parsing in BasePayload
        parsed_list = parse_address_list("one@example.com")
        self.assertEqual(len(parsed_list), 1)
        self.assertEqual(parsed_list[0].addr_spec, "one@example.com")

    def test_lazy_strings(self):
        parsed_list = parse_address_list([gettext_lazy('"Example, Inc." <one@example.com>')])
        self.assertEqual(len(parsed_list), 1)
        self.assertEqual(parsed_list[0].display_name, "Example, Inc.")
        self.assertEqual(parsed_list[0].addr_spec, "one@example.com")

        parsed_list = parse_address_list(gettext_lazy("one@example.com"))
        self.assertEqual(len(parsed_list), 1)
        self.assertEqual(parsed_list[0].display_name, "")
        self.assertEqual(parsed_list[0].addr_spec, "one@example.com")

    def test_parse_one(self):
        parsed = parse_single_address("one@example.com")
        self.assertEqual(parsed.address, "one@example.com")

        with self.assertRaisesMessage(AnymailInvalidAddress, "Only one email address is allowed; found 2"):
            parse_single_address("one@example.com, two@example.com")

        with self.assertRaisesMessage(AnymailInvalidAddress, "Invalid email address"):
            parse_single_address(" ")


class NormalizedAttachmentTests(SimpleTestCase):
    """Test utils.Attachment"""

    # (Several basic tests could be added here)

    def test_content_disposition_attachment(self):
        image = MIMEImage(b";-)", "x-emoticon")
        image["Content-Disposition"] = 'attachment; filename="emoticon.txt"'
        att = Attachment(image, "ascii")
        self.assertEqual(att.name, "emoticon.txt")
        self.assertEqual(att.content, b";-)")
        self.assertFalse(att.inline)
        self.assertIsNone(att.content_id)
        self.assertEqual(att.cid, "")

    def test_content_disposition_inline(self):
        image = MIMEImage(b";-)", "x-emoticon")
        image["Content-Disposition"] = 'inline'
        att = Attachment(image, "ascii")
        self.assertIsNone(att.name)
        self.assertEqual(att.content, b";-)")
        self.assertTrue(att.inline)  # even without the Content-ID
        self.assertIsNone(att.content_id)
        self.assertEqual(att.cid, "")

        image["Content-ID"] = "<abc123@example.net>"
        att = Attachment(image, "ascii")
        self.assertEqual(att.content_id, "<abc123@example.net>")
        self.assertEqual(att.cid, "abc123@example.net")

    def test_content_id_implies_inline(self):
        """A MIME object with a Content-ID should be assumed to be inline"""
        image = MIMEImage(b";-)", "x-emoticon")
        image["Content-ID"] = "<abc123@example.net>"
        att = Attachment(image, "ascii")
        self.assertTrue(att.inline)
        self.assertEqual(att.content_id, "<abc123@example.net>")

        # ... but not if explicit Content-Disposition says otherwise
        image["Content-Disposition"] = "attachment"
        att = Attachment(image, "ascii")
        self.assertFalse(att.inline)
        self.assertIsNone(att.content_id)  # ignored for non-inline Attachment


class LazyCoercionTests(SimpleTestCase):
    """Test utils.is_lazy and force_non_lazy*"""

    def test_is_lazy(self):
        self.assertTrue(is_lazy(gettext_lazy("lazy string is lazy")))

    def test_not_lazy(self):
        self.assertFalse(is_lazy("text not lazy"))
        self.assertFalse(is_lazy(b"bytes not lazy"))
        self.assertFalse(is_lazy(None))
        self.assertFalse(is_lazy({'dict': "not lazy"}))
        self.assertFalse(is_lazy(["list", "not lazy"]))
        self.assertFalse(is_lazy(object()))
        self.assertFalse(is_lazy([gettext_lazy("doesn't recurse")]))

    def test_force_lazy(self):
        result = force_non_lazy(gettext_lazy("text"))
        self.assertIsInstance(result, str)
        self.assertEqual(result, "text")

    def test_format_lazy(self):
        self.assertTrue(is_lazy(format_lazy("{0}{1}",
                                            gettext_lazy("concatenation"), gettext_lazy("is lazy"))))
        result = force_non_lazy(format_lazy("{first}/{second}",
                                            first=gettext_lazy("text"), second=gettext_lazy("format")))
        self.assertIsInstance(result, str)
        self.assertEqual(result, "text/format")

    def test_force_string(self):
        result = force_non_lazy("text")
        self.assertIsInstance(result, str)
        self.assertEqual(result, "text")

    def test_force_bytes(self):
        result = force_non_lazy(b"bytes \xFE")
        self.assertIsInstance(result, bytes)
        self.assertEqual(result, b"bytes \xFE")

    def test_force_none(self):
        result = force_non_lazy(None)
        self.assertIsNone(result)

    def test_force_dict(self):
        result = force_non_lazy_dict({'a': 1, 'b': gettext_lazy("b"),
                                      'c': {'c1': gettext_lazy("c1")}})
        self.assertEqual(result, {'a': 1, 'b': "b", 'c': {'c1': "c1"}})
        self.assertIsInstance(result['b'], str)
        self.assertIsInstance(result['c']['c1'], str)

    def test_force_list(self):
        result = force_non_lazy_list([0, gettext_lazy("b"), "c"])
        self.assertEqual(result, [0, "b", "c"])  # coerced to list
        self.assertIsInstance(result[1], str)


class UpdateDeepTests(SimpleTestCase):
    """Test utils.update_deep"""

    def test_updates_recursively(self):
        first = {'a': {'a1': 1, 'aa': {}}, 'b': "B"}
        second = {'a': {'a2': 2, 'aa': {'aa1': 11}}}
        result = update_deep(first, second)
        self.assertEqual(first, {'a': {'a1': 1, 'a2': 2, 'aa': {'aa1': 11}}, 'b': "B"})
        self.assertIsNone(result)  # modifies first in place; doesn't return it (same as dict.update())

    def test_overwrites_sequences(self):
        """Only mappings are handled recursively; sequences are considered atomic"""
        first = {'a': [1, 2]}
        second = {'a': [3]}
        update_deep(first, second)
        self.assertEqual(first, {'a': [3]})

    def test_handles_non_dict_mappings(self):
        """Mapping types in general are supported"""
        from collections import OrderedDict, defaultdict
        first = OrderedDict(a=OrderedDict(a1=1), c={'c1': 1})
        second = defaultdict(None, a=dict(a2=2))
        update_deep(first, second)
        self.assertEqual(first, {'a': {'a1': 1, 'a2': 2}, 'c': {'c1': 1}})


@override_settings(ALLOWED_HOSTS=[".example.com"])
class RequestUtilsTests(SimpleTestCase):
    """Test utils.get_request_* helpers"""

    def setUp(self):
        self.request_factory = RequestFactory()
        super(RequestUtilsTests, self).setUp()

    @staticmethod
    def basic_auth(username, password):
        """Return HTTP_AUTHORIZATION header value for basic auth with username, password"""
        credentials = base64.b64encode("{}:{}".format(username, password).encode('utf-8')).decode('utf-8')
        return "Basic {}".format(credentials)

    def test_get_request_basic_auth(self):
        # without auth:
        request = self.request_factory.post('/path/to/?query',
                                            HTTP_HOST='www.example.com',
                                            HTTP_SCHEME='https')
        self.assertIsNone(get_request_basic_auth(request))

        # with basic auth:
        request = self.request_factory.post('/path/to/?query',
                                            HTTP_HOST='www.example.com',
                                            HTTP_AUTHORIZATION=self.basic_auth('user', 'pass'))
        self.assertEqual(get_request_basic_auth(request), "user:pass")

        # with some other auth
        request = self.request_factory.post('/path/to/?query',
                                            HTTP_HOST='www.example.com',
                                            HTTP_AUTHORIZATION="Bearer abcde12345")
        self.assertIsNone(get_request_basic_auth(request))

    def test_get_request_uri(self):
        # without auth:
        request = self.request_factory.post('/path/to/?query', secure=True,
                                            HTTP_HOST='www.example.com')
        self.assertEqual(get_request_uri(request),
                         "https://www.example.com/path/to/?query")

        # with basic auth:
        request = self.request_factory.post('/path/to/?query', secure=True,
                                            HTTP_HOST='www.example.com',
                                            HTTP_AUTHORIZATION=self.basic_auth('user', 'pass'))
        self.assertEqual(get_request_uri(request),
                         "https://user:pass@www.example.com/path/to/?query")

    @override_settings(SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
                       USE_X_FORWARDED_HOST=True)
    def test_get_request_uri_with_proxy(self):
        request = self.request_factory.post('/path/to/?query', secure=False,
                                            HTTP_HOST='web1.internal',
                                            HTTP_X_FORWARDED_PROTO='https',
                                            HTTP_X_FORWARDED_HOST='secret.example.com:8989',
                                            HTTP_AUTHORIZATION=self.basic_auth('user', 'pass'))
        self.assertEqual(get_request_uri(request),
                         "https://user:pass@secret.example.com:8989/path/to/?query")


class QueryDictUtilsTests(SimpleTestCase):
    def test_querydict_getfirst(self):
        q = QueryDict("a=one&a=two&a=three")
        q.getfirst = querydict_getfirst.__get__(q)
        self.assertEqual(q.getfirst('a'), "one")

        # missing key exception:
        with self.assertRaisesMessage(KeyError, "not a key"):
            q.getfirst("not a key")

        # defaults:
        self.assertEqual(q.getfirst('not a key', "beta"), "beta")
        self.assertIsNone(q.getfirst('not a key', None))


class ParseRFC2822DateTests(SimpleTestCase):
    def test_with_timezones(self):
        dt = parse_rfc2822date("Tue, 24 Oct 2017 10:11:35 -0700")
        self.assertEqual(dt.isoformat(), "2017-10-24T10:11:35-07:00")
        self.assertIsNotNone(dt.utcoffset())  # aware

        dt = parse_rfc2822date("Tue, 24 Oct 2017 10:11:35 +0700")
        self.assertEqual(dt.isoformat(), "2017-10-24T10:11:35+07:00")
        self.assertIsNotNone(dt.utcoffset())  # aware

        dt = parse_rfc2822date("Tue, 24 Oct 2017 10:11:35 +0000")
        self.assertEqual(dt.isoformat(), "2017-10-24T10:11:35+00:00")
        self.assertIsNotNone(dt.tzinfo)  # aware

    def test_without_timezones(self):
        dt = parse_rfc2822date("Tue, 24 Oct 2017 10:11:35 -0000")  # "no timezone information"
        self.assertEqual(dt.isoformat(), "2017-10-24T10:11:35")
        self.assertIsNone(dt.tzinfo)  # naive (compare with +0000 version in previous test)

        dt = parse_rfc2822date("Tue, 24 Oct 2017 10:11:35")
        self.assertEqual(dt.isoformat(), "2017-10-24T10:11:35")
        self.assertIsNone(dt.tzinfo)  # naive

    def test_unparseable_dates(self):
        self.assertIsNone(parse_rfc2822date(""))
        self.assertIsNone(parse_rfc2822date("  "))
        self.assertIsNone(parse_rfc2822date("garbage"))
        self.assertIsNone(parse_rfc2822date("Tue, 24 Oct"))
        self.assertIsNone(parse_rfc2822date("Lug, 24 Nod 2017 10:11:35 +0000"))
        self.assertIsNone(parse_rfc2822date("Tue, 99 Oct 9999 99:99:99 +9999"))


class LazyErrorTests(SimpleTestCase):
    def test_attr(self):
        lazy = _LazyError(ValueError("lazy failure"))  # creating doesn't cause error
        lazy.some_prop = "foo"  # setattr doesn't cause error
        with self.assertRaisesMessage(ValueError, "lazy failure"):
            self.unused = lazy.anything  # getattr *does* cause error

    def test_call(self):
        lazy = _LazyError(ValueError("lazy failure"))  # creating doesn't cause error
        with self.assertRaisesMessage(ValueError, "lazy failure"):
            self.unused = lazy()  # call *does* cause error


class CaseInsensitiveCasePreservingDictTests(SimpleTestCase):
    def setUp(self):
        self.dict = CaseInsensitiveCasePreservingDict()
        self.dict["Accept"] = "application/text+xml"
        self.dict["accEPT"] = "application/json"

    def test_preserves_first_key(self):
        self.assertEqual(list(self.dict.keys()), ["Accept"])

    def test_copy(self):
        copy = self.dict.copy()
        self.assertIsNot(copy, self.dict)
        self.assertEqual(copy, self.dict)
        # Here's why the superclass CaseInsensitiveDict.copy is insufficient:
        self.assertIsInstance(copy, CaseInsensitiveCasePreservingDict)

    def test_get_item(self):
        self.assertEqual(self.dict["accept"], "application/json")
        self.assertEqual(self.dict["Accept"], "application/json")
        self.assertEqual(self.dict["accEPT"], "application/json")

    # The base CaseInsensitiveDict functionality is well-tested in Requests,
    # so we don't repeat it here.


class UnsetValueTests(SimpleTestCase):
    """Tests for the UNSET sentinel value"""

    def test_not_other_values(self):
        self.assertIsNot(UNSET, None)
        self.assertIsNot(UNSET, False)
        self.assertNotEqual(UNSET, 0)
        self.assertNotEqual(UNSET, "")

    def test_unset_survives_pickle(self):
        # Required for using AnymailMessage with django-mailer
        pickled = pickle.dumps(UNSET)
        self.assertIs(pickle.loads(pickled), UNSET)

    def test_unset_survives_copy(self):
        self.assertIs(copy.copy(UNSET), UNSET)
        self.assertIs(copy.deepcopy(UNSET), UNSET)

    def test_unset_has_useful_repr(self):
        # (something better than '<object object at ...>')
        self.assertIn("UNSET", repr(UNSET))

    def test_equality(self):
        # `is UNSET` is preferred to `== UNSET`, but both should work
        self.assertEqual(UNSET, UNSET)
