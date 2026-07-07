from cwl_spawn.sizing import (
    DEFAULT_INSTANCE_TYPE,
    build_truffle_argv,
    mib_to_gib,
    pick_instance_type,
)


def test_mib_to_gib():
    assert mib_to_gib(1024) == 1.0
    assert mib_to_gib(2048) == 2.0
    assert mib_to_gib(512) == 0.5
    # cwltool gives a number; strings that look numeric still coerce.
    assert mib_to_gib("4096") == 4.0
    # Missing / non-positive / junk => None (caller omits --min-memory).
    assert mib_to_gib(None) is None
    assert mib_to_gib(0) is None
    assert mib_to_gib(-1) is None
    assert mib_to_gib("nope") is None


def test_build_truffle_argv_rounds_memory_up():
    argv = build_truffle_argv(4, 3.2, "x86_64")
    assert argv[:4] == ["truffle", "search", "--pick-first", "--show-price"]
    assert argv[argv.index("--min-vcpu") + 1] == "4"
    # 3.2 GiB rounds up to 4 so we never under-provision.
    assert argv[argv.index("--min-memory") + 1] == "4"
    assert argv[argv.index("--architecture") + 1] == "x86_64"


def test_build_truffle_argv_omits_unset():
    argv = build_truffle_argv(None, None, None)
    assert argv == ["truffle", "search", "--pick-first", "--show-price"]


def test_pick_instance_type_override_wins():
    assert pick_instance_type(override="  c7g.8xlarge  ", cores=1) == "c7g.8xlarge"


def test_pick_instance_type_uses_truffle_result():
    def fake_runner(argv):
        assert "--min-vcpu" in argv
        return "c6i.2xlarge\n"

    assert pick_instance_type(cores=8, ram_mib=16384, runner=fake_runner) == "c6i.2xlarge"


def test_pick_instance_type_nothing_to_size_on_returns_default():
    assert pick_instance_type() == DEFAULT_INSTANCE_TYPE


def test_pick_instance_type_falls_back_on_runner_error():
    def boom(argv):
        raise RuntimeError("truffle blew up")

    assert pick_instance_type(cores=4, runner=boom) == DEFAULT_INSTANCE_TYPE
