"""Plain-text postings become headings, lists and paragraphs - and never raw HTML."""

from cvmaker.posting import posting_html


def test_headings_bullets_and_paragraphs():
    html = posting_html(
        "About us\nWe build payroll software for small companies.\n\n"
        "Requirements:\n- 3+ years of Java\n• SQL\n1. Docker\n\n"
        "We look forward to hearing from you."
    )
    assert html == (
        "<h4>About us</h4>\n<p>We build payroll software for small companies.</p>\n"
        "<h4>Requirements</h4>\n<ul>\n<li>3+ years of Java</li>\n<li>SQL</li>\n<li>Docker</li>\n</ul>\n"
        "<p>We look forward to hearing from you.</p>"
    )


def test_plain_lines_under_a_heading_read_as_a_list():
    html = posting_html("What we offer:\nFlexible hours\nRemote work\nTraining budget")
    assert html == "<h4>What we offer</h4>\n<ul>\n<li>Flexible hours</li>\n<li>Remote work</li>\n<li>Training budget</li>\n</ul>"


def test_wrapped_sentences_are_joined():
    assert posting_html("You will solve\nreal business problems.") == "<p>You will solve real business problems.</p>"


def test_posting_text_is_escaped():
    html = posting_html("Role:\n- <script>alert(1)</script>\n<img src=x onerror=alert(1)> is a long enough sentence, with punctuation.")
    assert "<script>" not in html and "<img" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_empty():
    assert posting_html(None) == "" and posting_html("\n\n  \n") == ""
