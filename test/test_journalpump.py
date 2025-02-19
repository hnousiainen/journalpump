from collections import OrderedDict
from datetime import datetime
from journalpump.journalpump import (default_json_serialization, ElasticsearchSender, FieldFilter, MsgBuffer, JournalObject,
                                     JournalObjectHandler, JournalPump, MAX_KAFKA_MESSAGE_SIZE, KafkaSender, LogplexSender,
                                     RsyslogSender)
from time import sleep
from unittest import mock, TestCase

import json


def test_journalpump_init(tmpdir):
    # Logplex sender
    journalpump_path = str(tmpdir.join("journalpump.json"))
    config = {
        "field_filters": {
            "filter_a": {
                "fields": ["message"]
            }
        },
        "readers": {
            "foo": {
                "senders": {
                    "bar": {
                        "field_filter": "filter_a",
                        "logplex_token": "foo",
                        "logplex_log_input_url": "http://logplex.com",
                        "output_type": "logplex",
                    },
                },
            },
        },
    }

    with open(journalpump_path, "w") as fp:
        fp.write(json.dumps(config))
    a = JournalPump(journalpump_path)

    assert len(a.field_filters) == 1
    assert len(a.readers) == 1
    for rn, r in a.readers.items():
        assert rn == "foo"
        r.running = False
        for sn, s in r.senders.items():
            assert sn == "bar"
            s.running = False
            assert isinstance(s, LogplexSender)
            assert s.field_filter.name == "filter_a"
            assert s.field_filter.fields == ["message"]

    # Kafka sender
    config = {
        "readers": {
            "foo": {
                "senders": {
                    "bar": {
                        "output_type": "kafka",
                        "logplex_token": "foo",
                        "kafka_address": "localhost",
                        "kafka_topic": "foo",
                    },
                },
            },
        },
    }
    with open(journalpump_path, "w") as fp:
        fp.write(json.dumps(config))
    a = JournalPump(journalpump_path)

    assert len(a.readers) == 1
    for rn, r in a.readers.items():
        assert rn == "foo"
        r.running = False
        for sn, s in r.senders.items():
            assert sn == "bar"
            s.running = False
            assert isinstance(s, KafkaSender)

    # Elasticsearch sender
    config = {
        "readers": {
            "foo": {
                "senders": {
                    "bar": {
                        "output_type": "elasticsearch",
                        "elasticsearch_url": "https://foo.aiven.io",
                        "elasticsearch_index_prefix": "fooprefix",
                    },
                },
            },
        },
    }
    with open(journalpump_path, "w") as fp:
        fp.write(json.dumps(config))
    a = JournalPump(journalpump_path)

    assert len(a.readers) == 1
    for rn, r in a.readers.items():
        assert rn == "foo"
        r.running = False
        for sn, s in r.senders.items():
            assert sn == "bar"
            s.running = False
            assert isinstance(s, ElasticsearchSender)

    # rsyslog sender
    config = {
        "readers": {
            "foo": {
                "senders": {
                    "bar": {
                        "output_type": "rsyslog",
                        "rsyslog_server": "127.0.0.1",
                        "rsyslog_port": 514,
                    },
                },
            },
        },
    }
    with open(journalpump_path, "w") as fp:
        fp.write(json.dumps(config))
    a = JournalPump(journalpump_path)

    assert len(a.readers) == 1
    for rn, r in a.readers.items():
        assert rn == "foo"
        r.running = False
        for sn, s in r.senders.items():
            assert sn == "bar"
            s.running = False
            assert isinstance(s, RsyslogSender)


def test_journal_reader_tagging(tmpdir):
    config = {
        "readers": {
            "system": {
                "journal_flags": ["SYSTEM"],
                "searches": [
                    {
                        "name": "kernel.cpu.temperature",
                        "fields": {
                            "MESSAGE": r"(?P<cpu>CPU\d+): .*temperature.*",
                            "SYSLOG_IDENTIFIER": r"^(?P<from>.*)$",
                            "PRIORITY": r"^(?P<level>[0-4])$",  # emergency, alert, critical, error
                            "SYSLOG_FACILITY": r"^0$",          # kernel only
                        },
                        "tags": {"section": "cputemp"},
                    },
                    {
                        "name": "noresults",
                        "fields": {
                            "MESSAGE": "(?P<msg>.*)",
                            "nosuchfield": ".*",
                        },
                    },
                ],
            },
        },
    }
    journalpump_path = str(tmpdir.join("journalpump.json"))
    with open(journalpump_path, "w") as fp:
        fp.write(json.dumps(config))
    pump = JournalPump(journalpump_path)
    reader = pump.readers["system"]

    # matching entry
    entry = JournalObject(entry={
        "MESSAGE": "CPU0: Core temperature above threshold, cpu clock throttled (total events = 1)",
        "PRIORITY": "2",
        "SYSLOG_FACILITY": "0",
        "SYSLOG_IDENTIFIER": "kernel",
    })
    result = reader.perform_searches(entry)
    expected = {
        "kernel.cpu.temperature": {
            "cpu": "CPU0",
            "from": "kernel",
            "level": "2",
            "section": "cputemp",
        }
    }
    assert result == expected

    # some fields are not matching
    entry = JournalObject(entry={
        "MESSAGE": "CPU1: on fire",
        "PRIORITY": "1",
        "SYSLOG_FACILITY": "0",
        "SYSLOG_IDENTIFIER": "kernel",
    })
    result = reader.perform_searches(entry)
    assert result == {}


class TestFieldFilter(TestCase):
    def test_whitelist(self):
        ff = FieldFilter("test", {"fields": ["_foo", "BAR"]})
        data = {"Foo": "a", "_bar": "b", "_zob": "c"}
        assert ff.filter_fields(data) == {"Foo": "a", "_bar": "b"}
        assert data == {"Foo": "a", "_bar": "b", "_zob": "c"}

    def test_blacklist(self):
        ff = FieldFilter("test", {"type": "blacklist", "fields": ["_foo"]})
        data = {"Foo": "a", "_bar": "b", "_zob": "c"}
        assert ff.filter_fields(data) == {"_bar": "b", "_zob": "c"}
        assert data == {"Foo": "a", "_bar": "b", "_zob": "c"}


class TestJournalObjectHandler(TestCase):
    def setUp(self):
        self.filter_a = FieldFilter("filter_a", {"fields": ["a"]})
        self.filter_b = FieldFilter("filter_b", {"fields": ["a", "b"]})
        self.sender_a = mock.Mock()
        self.sender_a.field_filter = self.filter_a
        self.sender_a.extra_field_values = {}
        self.sender_a.msg_buffer = MsgBuffer()
        self.sender_b = mock.Mock()
        self.sender_b.field_filter = self.filter_b
        self.sender_b.extra_field_values = {}
        self.sender_b.msg_buffer = MsgBuffer()
        self.sender_c = mock.Mock()
        self.sender_c.field_filter = None
        self.sender_c.extra_field_values = {}
        self.sender_c.msg_buffer = MsgBuffer()
        self.pump = mock.Mock()
        self.reader = mock.Mock()
        self.reader.senders = {"sender_a": self.sender_a, "sender_b": self.sender_b, "sender_c": self.sender_c}

    def test_filtered_processing(self):
        jobject = JournalObject(entry=OrderedDict(a=1, b=2, c=3, REALTIME_TIMESTAMP=1), cursor=10)
        handler = JournalObjectHandler(jobject, self.reader, self.pump)
        assert handler.process() is True
        assert (json.dumps({"a": 1}).encode("utf-8"), 10) in self.sender_a.msg_buffer.messages

        assert (json.dumps(OrderedDict(a=1, b=2)).encode("utf-8"), 10) in self.sender_b.msg_buffer.messages

        largest_data = json.dumps(
            OrderedDict(a=1, b=2, c=3, REALTIME_TIMESTAMP=1, timestamp=datetime.utcfromtimestamp(1)),
            default=default_json_serialization,
        ).encode("utf-8")
        assert len(self.sender_c.msg_buffer.messages) == 1
        self.reader.inc_line_stats.assert_called_once_with(journal_bytes=len(largest_data), journal_lines=1)

    def test_too_large_data(self):
        self.pump.make_tags.return_value = "tags"
        too_large = OrderedDict(a=1, b="x" * MAX_KAFKA_MESSAGE_SIZE)
        jobject = JournalObject(entry=too_large, cursor=10)
        handler = JournalObjectHandler(jobject, self.reader, self.pump)
        assert handler.process() is True
        assert (json.dumps({"a": 1}).encode("utf-8"), 10) in self.sender_a.msg_buffer.messages
        assert "too large message" in str(self.sender_b.msg_buffer.messages)

        self.pump.stats.increase.assert_called_once_with("journal.read_error", tags="tags")


def test_journalpump_state_file(tmpdir):
    journalpump_path = str(tmpdir.join("journalpump.json"))
    statefile_path = str(tmpdir.join("journalpump_state.json"))
    config = {
        "json_state_file_path": statefile_path,
        "readers": {
            "state_test": {
                "senders": {
                    "fake_syslog": {
                        "output_type": "rsyslog",
                        "rsyslog_server": "127.0.0.1",
                        "rsyslog_port": 514,
                    },
                },
            },
        },
    }

    with open(journalpump_path, "w") as fp:
        fp.write(json.dumps(config))

    pump = JournalPump(journalpump_path)
    for _, reader in pump.readers.items():
        reader.initialize_senders()
        sleep(1.1)
        reader.request_stop()
    pump.save_state()

    with open(statefile_path, "r") as fp:
        state = json.load(fp)

    assert "readers" in state
    assert "start_time" in state
    assert "state_test" in state["readers"]
    reader_state = state["readers"]["state_test"]
    assert reader_state.get("total_bytes") == 0
    assert reader_state.get("total_lines") == 0
    assert "senders" in reader_state
    assert "fake_syslog" in reader_state["senders"]
    sender_state = reader_state["senders"]["fake_syslog"]
    assert "health" in sender_state
    assert "elapsed" in sender_state["health"]
    assert sender_state["health"]["elapsed"] > 1.0
    assert "status" in sender_state["health"]
    assert sender_state["health"]["status"] == "stopped"
