from benchmark.prompts import PROMPT_SETS, ROLES, TIERS


def test_all_roles_present():
    assert set(ROLES) == {"builder", "security", "research", "general"}


def test_all_tiers_present():
    for role in ROLES:
        assert set(PROMPT_SETS[role].keys()) == set(TIERS), f"{role} missing tiers"


def test_two_prompts_per_tier():
    for role in ROLES:
        for tier in TIERS:
            prompts = PROMPT_SETS[role][tier]
            assert len(prompts) == 2, f"{role}/{tier} should have 2 prompts, got {len(prompts)}"


def test_all_prompts_are_nonempty_strings():
    for role in ROLES:
        for tier in TIERS:
            for i, p in enumerate(PROMPT_SETS[role][tier]):
                assert isinstance(p, str) and len(p) > 20, f"{role}/{tier}[{i}] is too short or not a string"
