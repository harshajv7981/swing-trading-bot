from swingbot.markets.cli import main


def test_cli_profiles_runs_halts_and_status(tmp_path, capsys):
    roots = ["--runs-root", str(tmp_path / "runs"), "--state-root", str(tmp_path / "state")]
    assert main(["profiles"]) == 0
    out = capsys.readouterr().out
    assert "nse_equities" in out and "us_equities" in out and "USD" in out

    assert main(["new-run", "us", "r1", *roots]) == 0
    assert (tmp_path / "runs/markets/us/r1/manifest.json").exists()
    assert main(["new-run", "us", "r1", *roots]) == 1          # frozen: never overwritten
    assert "already exists" in capsys.readouterr().err

    assert main(["halt", "us", "--reason", "testing", *roots]) == 0
    assert main(["status", *roots, "--broker-config", str(tmp_path / "none.toml")]) == 0
    out = capsys.readouterr().out
    assert "us: HALTED" in out and "nse: running" in out and "alpaca: disabled, paper" in out
    assert main(["resume", "us", *roots]) == 0
    main(["halt", "all", "--reason", "everything", *roots])
    main(["status", *roots, "--broker-config", str(tmp_path / "none.toml")])
    assert capsys.readouterr().out.count("HALTED") == 2


def test_alpaca_check_refuses_when_disabled(tmp_path, capsys):
    assert main(["alpaca-check", "--broker-config", str(tmp_path / "none.toml")]) == 1
    assert "not enabled" in capsys.readouterr().err
