from frame_cut.utils.time import format_time, parse_time


class TestTimeUtils:
    def test_format_time_mmss(self):
        assert format_time(65.0) == "01:05"
        assert format_time(0.0) == "00:00"
        assert format_time(59.5) == "00:59"

    def test_format_time_hhmmss(self):
        assert format_time(3661.0) == "01:01:01"
        assert format_time(7200.0) == "02:00:00"

    def test_format_time_with_millis(self):
        result = format_time(61.123, format="hh:mm:ss.ms")
        assert result == "00:01:01.123"

    def test_parse_time_mmss(self):
        assert parse_time("01:30") == 90.0

    def test_parse_time_hhmmss(self):
        assert parse_time("01:00:00") == 3600.0
