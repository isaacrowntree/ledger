"""Where the API listens, and that the debugger never faces a network."""
import pytest

from api.server import run_options


def test_default_is_this_machine_with_the_debugger():
    assert run_options({}) == {"host": "127.0.0.1", "port": 5050, "debug": True, "threaded": True}


def test_a_network_address_runs_without_the_debugger():
    opts = run_options({"LEDGER_HOST": "0.0.0.0", "LEDGER_PORT": "8000"})
    assert (opts["host"], opts["port"], opts["debug"]) == ("0.0.0.0", 8000, False)


def test_the_debugger_is_refused_off_loopback():
    with pytest.raises(SystemExit, match="loopback"):
        run_options({"LEDGER_HOST": "0.0.0.0", "LEDGER_DEBUG": "1"})


def test_debug_can_be_turned_off_locally():
    assert run_options({"LEDGER_DEBUG": "0"})["debug"] is False
