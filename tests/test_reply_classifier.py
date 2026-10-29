"""
Unit Tests for Reply Intelligence Classifier across all 10 Categories.
"""

import pytest
from src.intelligence.reply_classifier import reply_classifier, ReplyCategory


def test_classify_meeting_invite():
    subject = "Re: Prospective Summer 2027 Research Internship"
    body = "Dear Harshith, thank you for reaching out. Your background in SciML sounds very interesting. Let's schedule a call next week via Zoom to discuss. Here is my calendly.com link."
    category, conf, reasoning, action = reply_classifier.classify(subject, body)
    assert category == ReplyCategory.MEETING_INVITE
    assert conf >= 0.90


def test_classify_rejection():
    subject = "Re: Research Internship Inquiry"
    body = "Dear Harshith, thank you for your email. Unfortunately, our lab is full and we have no positions for interns next summer. I wish you the best in your search."
    category, conf, reasoning, action = reply_classifier.classify(subject, body)
    assert category == ReplyCategory.REJECTION
    assert conf >= 0.85


def test_classify_info_request():
    subject = "Re: Summer 2027 Internship Inquiry"
    body = "Hi Harshith, please attach your academic transcript and complete CV so we can evaluate your application with the admissions committee."
    category, conf, reasoning, action = reply_classifier.classify(subject, body)
    assert category == ReplyCategory.INFO_REQUEST
    assert conf >= 0.85


def test_classify_bounce():
    subject = "Delivery Status Notification (Failure)"
    body = "The following message to bodner@mit.edu was undeliverable. 550 User unknown in virtual mailbox table. Mail delivery failed."
    headers = {"from": "mailer-daemon@mit.edu"}
    category, conf, reasoning, action = reply_classifier.classify(subject, body, headers)
    assert category == ReplyCategory.BOUNCE
    assert conf >= 0.95


def test_classify_opt_out():
    subject = "Re: Research Inquiry"
    body = "Please unsubscribe me and remove me from your list. Do not contact me again."
    category, conf, reasoning, action = reply_classifier.classify(subject, body)
    assert category == ReplyCategory.OPT_OUT
    assert conf >= 0.95


def test_classify_auto_reply():
    subject = "Automatic reply: Research Internship"
    body = "I am currently out of office on annual leave until November 15th. I will respond to your email when I return."
    headers = {"auto-submitted": "auto-replied"}
    category, conf, reasoning, action = reply_classifier.classify(subject, body, headers)
    assert category == ReplyCategory.AUTO_REPLY
    assert conf >= 0.90


def test_classify_referral():
    subject = "Re: Summer 2027 Inquiry"
    body = "I suggest you write to Prof. Alan Edelman or apply through our central department summer internship program website."
    category, conf, reasoning, action = reply_classifier.classify(subject, body)
    assert category == ReplyCategory.REFERRAL
    assert conf >= 0.85


def test_classify_funding_question():
    subject = "Re: Visiting Student Position"
    body = "Do you have your own funding through Mitacs or DAAD, or does IIT Delhi provide a travel stipend? This is an unfunded position."
    category, conf, reasoning, action = reply_classifier.classify(subject, body)
    assert category == ReplyCategory.FUNDING_QUESTION
    assert conf >= 0.80


def test_classify_technical_question():
    subject = "Re: Neural Process Twin"
    body = "How did you implement the boundary condition enforcement in your PyTorch model, and what were the exact benchmark error metrics against FDM?"
    category, conf, reasoning, action = reply_classifier.classify(subject, body)
    assert category == ReplyCategory.TECHNICAL_QUESTION
    assert conf >= 0.80


def test_classify_interest():
    subject = "Re: Summer 2027 Research Internship"
    body = "Dear Harshith, this sounds very interesting and you have an impressive background in numerical methods. I would be happy to host you for Summer 2027."
    category, conf, reasoning, action = reply_classifier.classify(subject, body)
    assert category == ReplyCategory.INTEREST
    assert conf >= 0.80
