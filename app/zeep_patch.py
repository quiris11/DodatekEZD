import base64
from functools import cached_property
from zeep.wsdl.attachments import Attachment as _ZeepAttachment


def _patched_attachment_content(self):
    encoding = self.headers.get("Content-Transfer-Encoding", None)
    content = self._part.content
    if encoding == "base64":
        return base64.b64decode(content)
    return content


_patch = cached_property(_patched_attachment_content)
_patch.__set_name__(_ZeepAttachment, 'content')
_ZeepAttachment.content = _patch
