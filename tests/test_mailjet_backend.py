from base64 import b64encode
from decimal import Decimal
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage

from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings, tag

from anymail.exceptions import (AnymailAPIError, AnymailSerializationError,
                                AnymailUnsupportedFeature,
                                AnymailRequestsAPIError)
from anymail.message import attach_inline_image_file

from .mock_requests_backend import RequestsBackendMockAPITestCase, SessionSharingTestCases
from .utils import sample_image_content, sample_image_path, SAMPLE_IMAGE_FILENAME, AnymailTestMixin, decode_att


@tag('mailjet')
@override_settings(EMAIL_BACKEND='anymail.backends.mailjet.EmailBackend',
                   ANYMAIL={
                       'MAILJET_API_KEY': '',
                       'MAILJET_SECRET_KEY': ''
                   })
class MailjetBackendMockAPITestCase(RequestsBackendMockAPITestCase):
    DEFAULT_RAW_RESPONSE = b"""{
        "Sent": [{
            "Email": "to@example.com",
            "MessageID": 12345678901234567
        }]
    }"""

    DEFAULT_TEMPLATE_RESPONSE = b"""{
        "Count": 1,
        "Data": [{
            "Text-part": "text body",
            "Html-part": "html body",
            "MJMLContent": "",
            "Headers": {
                "Subject": "Hello World!",
                "SenderName": "Friendly Tester",
                "SenderEmail": "some@example.com",
                "ReplyEmail": ""
            }
        }],
        "Total": 1
    }"""

    def setUp(self):
        super().setUp()
        # Simple message useful for many tests
        self.message = mail.EmailMultiAlternatives('Subject', 'Text Body', 'from@example.com', ['to@example.com'])

    def set_template_response(self, status_code=200, raw=None):
        """Sets an expectation for a template and populate its response."""
        if raw is None:
            raw = self.DEFAULT_TEMPLATE_RESPONSE
        template_response = RequestsBackendMockAPITestCase.MockResponse(status_code, raw)
        self.mock_request.side_effect = iter([
            template_response,
            self.mock_request.return_value
        ])


@tag('mailjet')
class MailjetBackendStandardEmailTests(MailjetBackendMockAPITestCase):
    """Test backend support for Django standard email features"""

    def test_send_mail(self):
        """Test basic API for simple send"""
        mail.send_mail('Subject here', 'Here is the message.',
                       'from@sender.example.com', ['to@example.com'], fail_silently=False)
        self.assert_esp_called('/v3/send')
        data = self.get_api_call_json()
        self.assertEqual(data['Subject'], "Subject here")
        self.assertEqual(data['Text-part'], "Here is the message.")
        self.assertEqual(data['FromEmail'], "from@sender.example.com")
        self.assertEqual(data['To'], "to@example.com")

    def test_name_addr(self):
        """Make sure RFC2822 name-addr format (with display-name) is allowed

        (Test both sender and recipient addresses)
        """
        msg = mail.EmailMessage(
            'Subject', 'Message', 'From Name <from@example.com>',
            ['Recipient #1 <to1@example.com>', 'to2@example.com'],
            cc=['Carbon Copy <cc1@example.com>', 'cc2@example.com'],
            bcc=['Blind Copy <bcc1@example.com>', 'bcc2@example.com'])
        msg.send()
        data = self.get_api_call_json()
        # See https://dev.mailjet.com/guides/#sending-a-basic-email
        self.assertEqual(data['FromName'], 'From Name')
        self.assertEqual(data['FromEmail'], 'from@example.com')
        self.assertEqual(data['To'], 'Recipient #1 <to1@example.com>, to2@example.com')
        self.assertEqual(data['Cc'], 'Carbon Copy <cc1@example.com>, cc2@example.com')
        self.assertEqual(data['Bcc'], 'Blind Copy <bcc1@example.com>, bcc2@example.com')

    def test_comma_in_display_name(self):
        # Mailjet 3.0 API doesn't properly parse RFC-2822 quoted display-names from To/Cc/Bcc:
        # `To: "Recipient, Ltd." <to@example.com>` tries to send messages to `"Recipient`
        # and to `Ltd.` (neither of which are actual email addresses).
        # As a workaround, force MIME "encoded-word" utf-8 encoding, which gets past Mailjet's broken parsing.
        # (This shouldn't be necessary in Mailjet 3.1, where Name becomes a separate json field for Cc/Bcc.)
        msg = mail.EmailMessage(
            'Subject', 'Message', '"Example, Inc." <from@example.com>',
            ['"Recipient, Ltd." <to@example.com>'],
            cc=['"This is a very long display name, intended to test our workaround does not insert carriage returns'
                ' or newlines into the encoded value, which would cause other problems" <long@example.com']
        )
        msg.send()
        data = self.get_api_call_json()
        self.assertEqual(data['FromName'], 'Example, Inc.')
        self.assertEqual(data['FromEmail'], 'from@example.com')
        # self.assertEqual(data['To'], '"Recipient, Ltd." <to@example.com>')  # this doesn't work
        self.assertEqual(data['To'], '=?utf-8?q?Recipient=2C_Ltd=2E?= <to@example.com>')  # workaround
        self.assertEqual(data['Cc'], '=?utf-8?q?This_is_a_very_long_display_name=2C_intended_to_test_our_workaround'
                                     '_does_not_insert_carriage_returns_or_newlines_into_the_encoded_value=2C_which'
                                     '_would_cause_other_problems?= <long@example.com>')

    def test_email_message(self):
        email = mail.EmailMessage(
            'Subject', 'Body goes here', 'from@example.com',
            ['to1@example.com', 'Also To <to2@example.com>'],
            bcc=['bcc1@example.com', 'Also BCC <bcc2@example.com>'],
            cc=['cc1@example.com', 'Also CC <cc2@example.com>'],
            headers={'Reply-To': 'another@example.com',
                     'X-MyHeader': 'my value'})
        email.send()
        data = self.get_api_call_json()
        self.assertEqual(data['Subject'], "Subject")
        self.assertEqual(data['Text-part'], "Body goes here")
        self.assertEqual(data['FromEmail'], "from@example.com")
        self.assertEqual(data['To'], 'to1@example.com, Also To <to2@example.com>')
        self.assertEqual(data['Bcc'], 'bcc1@example.com, Also BCC <bcc2@example.com>')
        self.assertEqual(data['Cc'], 'cc1@example.com, Also CC <cc2@example.com>')
        self.assertCountEqual(data['Headers'], {
            'Reply-To': 'another@example.com',
            'X-MyHeader': 'my value',
        })

    def test_html_message(self):
        text_content = 'This is an important message.'
        html_content = '<p>This is an <strong>important</strong> message.</p>'
        email = mail.EmailMultiAlternatives('Subject', text_content,
                                            'from@example.com', ['to@example.com'])
        email.attach_alternative(html_content, "text/html")
        email.send()
        data = self.get_api_call_json()
        self.assertEqual(data['Text-part'], text_content)
        self.assertEqual(data['Html-part'], html_content)
        # Don't accidentally send the html part as an attachment:
        self.assertNotIn('Attachments', data)

    def test_html_only_message(self):
        html_content = '<p>This is an <strong>important</strong> message.</p>'
        email = mail.EmailMessage('Subject', html_content, 'from@example.com', ['to@example.com'])
        email.content_subtype = "html"  # Main content is now text/html
        email.send()
        data = self.get_api_call_json()
        self.assertNotIn('Text-part', data)
        self.assertEqual(data['Html-part'], html_content)

    def test_extra_headers(self):
        self.message.extra_headers = {'X-Custom': 'string', 'X-Num': 123}
        self.message.send()
        data = self.get_api_call_json()
        self.assertCountEqual(data['Headers'], {
            'X-Custom': 'string',
            'X-Num': 123,
        })

    def test_extra_headers_serialization_error(self):
        self.message.extra_headers = {'X-Custom': Decimal(12.5)}
        with self.assertRaisesMessage(AnymailSerializationError, "Decimal"):
            self.message.send()

    def test_reply_to(self):
        email = mail.EmailMessage('Subject', 'Body goes here', 'from@example.com', ['to1@example.com'],
                                  reply_to=['reply@example.com', 'Other <reply2@example.com>'],
                                  headers={'X-Other': 'Keep'})
        email.send()
        data = self.get_api_call_json()
        self.assertEqual(data['Headers'], {
            'Reply-To': 'reply@example.com, Other <reply2@example.com>',
            'X-Other': 'Keep'
        })  # don't lose other headers

    def test_attachments(self):
        text_content = "* Item one\n* Item two\n* Item three"
        self.message.attach(filename="test.txt", content=text_content, mimetype="text/plain")

        # Should guess mimetype if not provided...
        png_content = b"PNG\xb4 pretend this is the contents of a png file"
        self.message.attach(filename="test.png", content=png_content)

        # Should work with a MIMEBase object (also tests no filename)...
        pdf_content = b"PDF\xb4 pretend this is valid pdf data"
        mimeattachment = MIMEBase('application', 'pdf')
        mimeattachment.set_payload(pdf_content)
        self.message.attach(mimeattachment)

        self.message.send()
        data = self.get_api_call_json()
        attachments = data['Attachments']
        self.assertEqual(len(attachments), 3)
        self.assertEqual(attachments[0]["Filename"], "test.txt")
        self.assertEqual(attachments[0]["Content-type"], "text/plain")
        self.assertEqual(decode_att(attachments[0]["content"]).decode('ascii'), text_content)
        self.assertNotIn('ContentID', attachments[0])

        self.assertEqual(attachments[1]["Content-type"], "image/png")  # inferred from filename
        self.assertEqual(attachments[1]["Filename"], "test.png")
        self.assertEqual(decode_att(attachments[1]["content"]), png_content)
        self.assertNotIn('ContentID', attachments[1])  # make sure image not treated as inline

        self.assertEqual(attachments[2]["Content-type"], "application/pdf")
        self.assertEqual(attachments[2]["Filename"], "")  # none
        self.assertEqual(decode_att(attachments[2]["content"]), pdf_content)
        self.assertNotIn('ContentID', attachments[2])

    def test_unicode_attachment_correctly_decoded(self):
        self.message.attach("Une pièce jointe.html", '<p>\u2019</p>', mimetype='text/html')
        self.message.send()
        data = self.get_api_call_json()
        self.assertEqual(data['Attachments'], [{
            'Filename': 'Une pièce jointe.html',
            'Content-type': 'text/html',
            'content': b64encode('<p>\u2019</p>'.encode('utf-8')).decode('ascii')
        }])

    def test_embedded_images(self):
        image_filename = SAMPLE_IMAGE_FILENAME
        image_path = sample_image_path(image_filename)
        image_data = sample_image_content(image_filename)

        cid = attach_inline_image_file(self.message, image_path)  # Read from a png file
        html_content = '<p>This has an <img src="cid:%s" alt="inline" /> image.</p>' % cid
        self.message.attach_alternative(html_content, "text/html")

        self.message.send()
        data = self.get_api_call_json()
        self.assertEqual(data['Html-part'], html_content)

        attachments = data['Inline_attachments']
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]['Filename'], cid)
        self.assertEqual(attachments[0]['Content-type'], 'image/png')
        self.assertEqual(decode_att(attachments[0]["content"]), image_data)

    def test_attached_images(self):
        image_filename = SAMPLE_IMAGE_FILENAME
        image_path = sample_image_path(image_filename)
        image_data = sample_image_content(image_filename)

        self.message.attach_file(image_path)  # option 1: attach as a file

        image = MIMEImage(image_data)  # option 2: construct the MIMEImage and attach it directly
        self.message.attach(image)

        image_data_b64 = b64encode(image_data).decode('ascii')

        self.message.send()
        data = self.get_api_call_json()
        self.assertEqual(data['Attachments'], [
            {
                'Filename': image_filename,  # the named one
                'Content-type': 'image/png',
                'content': image_data_b64,
            },
            {
                'Filename': '',  # the unnamed one
                'Content-type': 'image/png',
                'content': image_data_b64,
            },
        ])

    def test_multiple_html_alternatives(self):
        # Multiple alternatives not allowed
        self.message.attach_alternative("<p>First html is OK</p>", "text/html")
        self.message.attach_alternative("<p>But not second html</p>", "text/html")
        with self.assertRaises(AnymailUnsupportedFeature):
            self.message.send()

    def test_html_alternative(self):
        # Only html alternatives allowed
        self.message.attach_alternative("{'not': 'allowed'}", "application/json")
        with self.assertRaises(AnymailUnsupportedFeature):
            self.message.send()

    def test_alternatives_fail_silently(self):
        # Make sure fail_silently is respected
        self.message.attach_alternative("{'not': 'allowed'}", "application/json")
        sent = self.message.send(fail_silently=True)
        self.assert_esp_not_called("API should not be called when send fails silently")
        self.assertEqual(sent, 0)

    def test_suppress_empty_address_lists(self):
        """Empty to, cc, bcc, and reply_to shouldn't generate empty fields"""
        self.message.send()
        data = self.get_api_call_json()
        self.assertNotIn('Cc', data)
        self.assertNotIn('Bcc', data)
        self.assertNotIn('ReplyTo', data)

        # Test empty `to` -- but send requires at least one recipient somewhere (like cc)
        self.message.to = []
        self.message.cc = ['cc@example.com']
        self.message.send()
        data = self.get_api_call_json()
        self.assertNotIn('To', data)
        self.assertEqual(data['Cc'], 'cc@example.com')

    def test_api_failure(self):
        self.set_mock_response(status_code=500)
        with self.assertRaisesMessage(AnymailAPIError, "Mailjet API response 500"):
            mail.send_mail('Subject', 'Body', 'from@example.com', ['to@example.com'])

        # Make sure fail_silently is respected
        self.set_mock_response(status_code=500)
        sent = mail.send_mail('Subject', 'Body', 'from@example.com', ['to@example.com'], fail_silently=True)
        self.assertEqual(sent, 0)

    def test_api_error_includes_details(self):
        """AnymailAPIError should include ESP's error message"""
        # JSON error response:
        error_response = b"""{
            "ErrorCode": 451,
            "Message": "Helpful explanation from Mailjet."
        }"""
        self.set_mock_response(status_code=200, raw=error_response)
        with self.assertRaisesMessage(AnymailAPIError, "Helpful explanation from Mailjet"):
            self.message.send()

        # Non-JSON error response:
        self.set_mock_response(status_code=500, raw=b"Ack! Bad proxy!")
        with self.assertRaisesMessage(AnymailAPIError, "Ack! Bad proxy!"):
            self.message.send()

        # No content in the error response:
        self.set_mock_response(status_code=502, raw=None)
        with self.assertRaises(AnymailAPIError):
            self.message.send()

    def test_invalid_api_key(self):
        """Anymail should add a helpful message for an invalid API key"""
        # Mailjet just returns a 401 error -- without additional explanation --
        # for invalid keys. We want to provide users something more helpful
        # than just "Mailjet API response 401:
        self.set_mock_response(status_code=401, reason="Unauthorized", raw=None)
        with self.assertRaisesMessage(AnymailAPIError, "Invalid Mailjet API key or secret"):
            self.message.send()


@tag('mailjet')
class MailjetBackendAnymailFeatureTests(MailjetBackendMockAPITestCase):
    """Test backend support for Anymail added features"""

    def test_envelope_sender(self):
        self.message.envelope_sender = "bounce-handler@bounces.example.com"
        self.message.send()
        data = self.get_api_call_json()
        self.assertEqual(data['Sender'], "bounce-handler@bounces.example.com")

    def test_metadata(self):
        # Mailjet expects the payload to be a single string
        # https://dev.mailjet.com/guides/#tagging-email-messages
        self.message.metadata = {'user_id': "12345", 'items': 6}
        self.message.send()
        data = self.get_api_call_json()
        self.assertJSONEqual(data['Mj-EventPayLoad'], {"user_id": "12345", "items": 6})

    def test_send_at(self):
        self.message.send_at = 1651820889  # 2022-05-06 07:08:09 UTC
        with self.assertRaisesMessage(AnymailUnsupportedFeature, 'send_at'):
            self.message.send()

    def test_tags(self):
        self.message.tags = ["receipt"]
        self.message.send()
        data = self.get_api_call_json()
        self.assertEqual(data['Mj-campaign'], "receipt")

        self.message.tags = ["receipt", "repeat-user"]
        with self.assertRaisesMessage(AnymailUnsupportedFeature, 'multiple tags'):
            self.message.send()

    def test_track_opens(self):
        self.message.track_opens = True
        self.message.send()
        data = self.get_api_call_json()
        self.assertEqual(data['Mj-trackopen'], 2)

    def test_track_clicks(self):
        self.message.track_clicks = True
        self.message.send()
        data = self.get_api_call_json()
        self.assertEqual(data['Mj-trackclick'], 2)

        # Also explicit "None" for False (to override server default)
        self.message.track_clicks = False
        self.message.send()
        data = self.get_api_call_json()
        self.assertEqual(data['Mj-trackclick'], 1)

    def test_template(self):
        # template_id can be str or int (but must be numeric ID -- not the template's name)
        self.message.template_id = '1234567'
        self.message.merge_global_data = {'name': "Alice", 'group': "Developers"}
        self.message.send()
        data = self.get_api_call_json()
        self.assertEqual(data['Mj-TemplateID'], '1234567')
        self.assertEqual(data['Vars'], {'name': "Alice", 'group': "Developers"})

    def test_template_populate_from_sender(self):
        self.set_template_response()
        self.message.template_id = '1234567'
        self.message.from_email = None
        self.message.send()
        data = self.get_api_call_json()
        self.assertEqual(data['Mj-TemplateID'], '1234567')
        self.assertEqual(data['FromName'], 'Friendly Tester')
        self.assertEqual(data['FromEmail'], 'some@example.com')

    def test_template_populate_from(self):
        # Note: Mailjet fails to properly quote the From field's display-name
        # if the template sender name contains commas (as shown here):
        template_response_content = b'''{
            "Count": 1,
            "Data": [{
                "Text-part": "text body",
                "Html-part": "html body",
                "MJMLContent": "",
                "Headers": {
                    "Subject": "Hello World!!",
                    "From": "Widgets, Inc. <noreply@example.com>",
                    "Reply-To": ""
                }
            }],
            "Total": 1
        }'''
        self.set_template_response(raw=template_response_content)
        self.message.template_id = '1234568'
        self.message.from_email = None
        self.message.send()
        data = self.get_api_call_json()
        self.assertEqual(data['Mj-TemplateID'], '1234568')
        self.assertEqual(data['FromName'], 'Widgets, Inc.')
        self.assertEqual(data['FromEmail'], 'noreply@example.com')

    def test_template_not_found(self):
        template_response_content = b'''{
            "ErrorInfo": "",
            "ErrorMessage": "Object not found",
            "StatusCode": 404
        }'''
        self.set_template_response(status_code=404, raw=template_response_content)
        self.message.template_id = '1234560'
        self.message.from_email = None
        with self.assertRaises(AnymailRequestsAPIError):
            self.message.send()

    def test_template_unexpected_response(self):
        # Missing headers (not sure if possible though).
        template_response_content = b'''{
            "Count": 1,
            "Data": [{
                "Text-part": "text body",
                "Html-part": "html body",
                "MJMLContent": "",
                "Headers": {
                }
            }],
            "Total": 1
        }'''
        self.set_template_response(raw=template_response_content)
        self.message.template_id = '1234561'
        self.message.from_email = None
        with self.assertRaisesMessage(AnymailRequestsAPIError, "template API"):
            self.message.send()

    def test_template_invalid_response(self):
        """Test scenario when MJ service returns no JSON for some reason."""
        template_response_content = b'''total garbage'''
        self.set_template_response(raw=template_response_content)
        self.message.template_id = '1234562'
        self.message.from_email = None
        with self.assertRaisesMessage(AnymailRequestsAPIError, "Invalid JSON"):
            self.message.send()

    def test_merge_data(self):
        self.message.to = ['alice@example.com', 'Bob <bob@example.com>']
        self.message.cc = ['cc@example.com']
        self.message.template_id = '1234567'
        self.message.merge_data = {
            'alice@example.com': {'name': "Alice", 'group': "Developers"},
            'bob@example.com': {'name': "Bob"},
        }
        self.message.merge_global_data = {'group': "Users", 'site': "ExampleCo"}
        self.message.send()

        data = self.get_api_call_json()
        messages = data['Messages']
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]['To'], 'alice@example.com')
        self.assertEqual(messages[0]['Cc'], 'cc@example.com')
        self.assertEqual(messages[0]['Mj-TemplateID'], '1234567')
        self.assertEqual(messages[0]['Vars'],
                         {'name': "Alice", 'group': "Developers", 'site': "ExampleCo"})

        self.assertEqual(messages[1]['To'], 'Bob <bob@example.com>')
        self.assertEqual(messages[1]['Cc'], 'cc@example.com')
        self.assertEqual(messages[1]['Mj-TemplateID'], '1234567')
        self.assertEqual(messages[1]['Vars'],
                         {'name': "Bob", 'group': "Users", 'site': "ExampleCo"})

    def test_merge_metadata(self):
        self.message.to = ['alice@example.com', 'Bob <bob@example.com>']
        self.message.merge_metadata = {
            'alice@example.com': {'order_id': 123, 'tier': 'premium'},
            'bob@example.com': {'order_id': 678},
        }
        self.message.metadata = {'notification_batch': 'zx912'}
        self.message.send()

        data = self.get_api_call_json()
        messages = data['Messages']
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]['To'], 'alice@example.com')
        # metadata and merge_metadata[recipient] are combined:
        self.assertJSONEqual(messages[0]['Mj-EventPayLoad'],
                             {'order_id': 123, 'tier': 'premium', 'notification_batch': 'zx912'})
        self.assertEqual(messages[1]['To'], 'Bob <bob@example.com>')
        self.assertJSONEqual(messages[1]['Mj-EventPayLoad'],
                             {'order_id': 678, 'notification_batch': 'zx912'})

    def test_default_omits_options(self):
        """Make sure by default we don't send any ESP-specific options.

        Options not specified by the caller should be omitted entirely from
        the API call (*not* sent as False or empty). This ensures
        that your ESP account settings apply by default.
        """
        self.message.send()
        data = self.get_api_call_json()
        self.assertNotIn('Mj-campaign', data)
        self.assertNotIn('Mj-EventPayLoad', data)
        self.assertNotIn('Mj-TemplateID', data)
        self.assertNotIn('Vars', data)
        self.assertNotIn('Mj-trackopen', data)
        self.assertNotIn('Mj-trackclick', data)

    def test_esp_extra(self):
        self.message.esp_extra = {
            'MJ-TemplateErrorDeliver': True,
            'MJ-TemplateErrorReporting': 'bugs@example.com'
        }
        self.message.send()
        data = self.get_api_call_json()
        self.assertEqual(data['MJ-TemplateErrorDeliver'], True)
        self.assertEqual(data['MJ-TemplateErrorReporting'], 'bugs@example.com')

    # noinspection PyUnresolvedReferences
    def test_send_attaches_anymail_status(self):
        """ The anymail_status should be attached to the message when it is sent """
        response_content = b"""{
            "Sent": [{
                "Email": "to1@example.com",
                "MessageID": 12345678901234500
            }]
        }"""
        self.set_mock_response(raw=response_content)
        msg = mail.EmailMessage('Subject', 'Message', 'from@example.com', ['to1@example.com'],)
        sent = msg.send()
        self.assertEqual(sent, 1)
        self.assertEqual(msg.anymail_status.status, {'sent'})
        self.assertEqual(msg.anymail_status.message_id, "12345678901234500")
        self.assertEqual(msg.anymail_status.recipients['to1@example.com'].status, 'sent')
        self.assertEqual(msg.anymail_status.recipients['to1@example.com'].message_id, "12345678901234500")
        self.assertEqual(msg.anymail_status.esp_response.content, response_content)

    # noinspection PyUnresolvedReferences
    def test_status_includes_all_recipients(self):
        """The status should include an entry for each recipient"""
        # Note that Mailjet's response only communicates "Sent" status; not failed addresses.
        # (This is an example response from before the workaround for commas in display-names...)
        response_content = b"""{
            "Sent": [{
                "Email": "to1@example.com",
                "MessageID": 12345678901234500
            }, {
                "Email": "\\"Recipient",
                "MessageID": 12345678901234501
            }, {
                "Email": "Also",
                "MessageID": 12345678901234502
            }]
        }"""
        self.set_mock_response(raw=response_content)
        msg = mail.EmailMessage('Subject', 'Message', 'from@example.com',
                                ['to1@example.com', '"Recipient, Also" <to2@example.com>'],)
        sent = msg.send()
        self.assertEqual(sent, 1)
        self.assertEqual(msg.anymail_status.status, {'sent', 'unknown'})
        self.assertEqual(msg.anymail_status.recipients['to1@example.com'].status, 'sent')
        self.assertEqual(msg.anymail_status.recipients['to1@example.com'].message_id, "12345678901234500")
        self.assertEqual(msg.anymail_status.recipients['to2@example.com'].status, 'unknown')  # because, whoops
        self.assertEqual(msg.anymail_status.recipients['to2@example.com'].message_id, None)
        self.assertEqual(msg.anymail_status.message_id,
                         {"12345678901234500", "12345678901234501", "12345678901234502", None})
        self.assertEqual(msg.anymail_status.esp_response.content, response_content)

    # noinspection PyUnresolvedReferences
    def test_send_failed_anymail_status(self):
        """ If the send fails, anymail_status should contain initial values"""
        self.set_mock_response(status_code=500)
        sent = self.message.send(fail_silently=True)
        self.assertEqual(sent, 0)
        self.assertIsNone(self.message.anymail_status.status)
        self.assertIsNone(self.message.anymail_status.message_id)
        self.assertEqual(self.message.anymail_status.recipients, {})
        self.assertIsNone(self.message.anymail_status.esp_response)

    # noinspection PyUnresolvedReferences
    def test_send_unparsable_response(self):
        """If the send succeeds, but a non-JSON API response, should raise an API exception"""
        mock_response = self.set_mock_response(status_code=200,
                                               raw=b"yikes, this isn't a real response")
        with self.assertRaises(AnymailAPIError):
            self.message.send()
        self.assertIsNone(self.message.anymail_status.status)
        self.assertIsNone(self.message.anymail_status.message_id)
        self.assertEqual(self.message.anymail_status.recipients, {})
        self.assertEqual(self.message.anymail_status.esp_response, mock_response)

    def test_json_serialization_errors(self):
        """Try to provide more information about non-json-serializable data"""
        self.message.tags = [Decimal('19.99')]  # yeah, don't do this
        with self.assertRaises(AnymailSerializationError) as cm:
            self.message.send()
            print(self.get_api_call_json())
        err = cm.exception
        self.assertIsInstance(err, TypeError)  # compatibility with json.dumps
        self.assertIn("Don't know how to send this data to Mailjet", str(err))  # our added context
        self.assertRegex(str(err), r"Decimal.*is not JSON serializable")  # original message

    def test_merge_data_null_values(self):
        # Mailjet doesn't accept None (null) as a merge value;
        # returns "HTTP/1.1 500 Cannot convert data from Null value"
        self.message.merge_global_data = {'Some': None}
        self.set_mock_response(status_code=500, reason="Cannot convert data from Null value", raw=None)
        with self.assertRaisesMessage(AnymailAPIError, "Cannot convert data from Null value"):
            self.message.send()


@tag('mailjet')
class MailjetBackendSessionSharingTestCase(SessionSharingTestCases, MailjetBackendMockAPITestCase):
    """Requests session sharing tests"""
    pass  # tests are defined in SessionSharingTestCases


@tag('mailjet')
@override_settings(EMAIL_BACKEND="anymail.backends.mailjet.EmailBackend")
class MailjetBackendImproperlyConfiguredTests(AnymailTestMixin, SimpleTestCase):
    """Test ESP backend without required settings in place"""

    def test_missing_api_key(self):
        with self.assertRaises(ImproperlyConfigured) as cm:
            mail.send_mail('Subject', 'Message', 'from@example.com', ['to@example.com'])
        errmsg = str(cm.exception)
        self.assertRegex(errmsg, r'\bMAILJET_API_KEY\b')

    @override_settings(ANYMAIL={'MAILJET_API_KEY': 'dummy'})
    def test_missing_secret_key(self):
        with self.assertRaises(ImproperlyConfigured) as cm:
            mail.send_mail('Subject', 'Message', 'from@example.com', ['to@example.com'])
        errmsg = str(cm.exception)
        self.assertRegex(errmsg, r'\bMAILJET_SECRET_KEY\b')
