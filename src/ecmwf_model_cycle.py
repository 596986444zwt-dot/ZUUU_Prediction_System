from datetime import datetime, timezone


# ==========================================================
# ECMWF IFS Model Cycle Mapping V1
# ==========================================================
#
# 本项目历史范围：
#
# 2024-03-14 →
#
# 官方切换：
#
# 48r1 已于 2023-06-27 06Z operational
#
# 49r1：
# 2024-11-12 06Z
#
# 50r1：
# 2026-05-12 06Z
#
# 因此本项目历史数据库只涉及：
#
# 48r1
# 49r1
# 50r1
#
# ==========================================================


MAPPING_VERSION = (
    "ECMWF_IFS_CYCLE_MAPPING_V1"
)


HISTORICAL_START = datetime(
    2024,
    3,
    14,
    0,
    0,
    tzinfo=timezone.utc,
)


CYCLE_49R1_START = datetime(
    2024,
    11,
    12,
    6,
    0,
    tzinfo=timezone.utc,
)


CYCLE_50R1_START = datetime(
    2026,
    5,
    12,
    6,
    0,
    tzinfo=timezone.utc,
)


def ensure_utc(dt):

    if not isinstance(
        dt,
        datetime
    ):
        raise TypeError(
            "run_time必须是datetime"
        )

    if dt.tzinfo is None:
        raise ValueError(
            "run_time必须包含timezone"
        )

    return dt.astimezone(
        timezone.utc
    )


def get_ecmwf_model_cycle(
    run_time
):

    run_time = ensure_utc(
        run_time
    )

    if run_time < HISTORICAL_START:

        raise ValueError(
            "当前Mapping V1不处理"
            "2024-03-14之前的数据："
            f"{run_time.isoformat()}"
        )

    if (
        run_time
        >= CYCLE_50R1_START
    ):

        return "50r1"

    if (
        run_time
        >= CYCLE_49R1_START
    ):

        return "49r1"

    return "48r1"


def get_mapping_version():

    return MAPPING_VERSION


def describe_mapping():

    return {

        "mapping_version":
            MAPPING_VERSION,

        "historical_start":
            HISTORICAL_START.isoformat(),

        "48r1": {
            "from":
                HISTORICAL_START.isoformat(),

            "to":
                CYCLE_49R1_START.isoformat(),
        },

        "49r1": {
            "from":
                CYCLE_49R1_START.isoformat(),

            "to":
                CYCLE_50R1_START.isoformat(),
        },

        "50r1": {
            "from":
                CYCLE_50R1_START.isoformat(),

            "to":
                None,
        },
    }


# ==========================================================
# Self Test
# ==========================================================

def self_test():

    cases = [

        # 历史起点
        (
            datetime(
                2024, 3, 14, 0,
                tzinfo=timezone.utc
            ),
            "48r1",
        ),

        # 49r1切换前最后一个Run
        (
            datetime(
                2024, 11, 12, 0,
                tzinfo=timezone.utc
            ),
            "48r1",
        ),

        # 49r1第一个Run
        (
            datetime(
                2024, 11, 12, 6,
                tzinfo=timezone.utc
            ),
            "49r1",
        ),

        (
            datetime(
                2025, 1, 1, 0,
                tzinfo=timezone.utc
            ),
            "49r1",
        ),

        # 50r1切换前最后一个Run
        (
            datetime(
                2026, 5, 12, 0,
                tzinfo=timezone.utc
            ),
            "49r1",
        ),

        # 50r1第一个Run
        (
            datetime(
                2026, 5, 12, 6,
                tzinfo=timezone.utc
            ),
            "50r1",
        ),

        # 当前项目测试Run
        (
            datetime(
                2026, 9, 20, 0,
                tzinfo=timezone.utc
            ),
            "50r1",
        ),
    ]

    print("=" * 78)

    print(
        "ECMWF IFS Model Cycle "
        "Mapping V1"
    )

    print("=" * 78)

    print(
        f"Mapping Version："
        f"{MAPPING_VERSION}"
    )

    print()

    for (
        run_time,
        expected
    ) in cases:

        actual = (
            get_ecmwf_model_cycle(
                run_time
            )
        )

        print(
            f"{run_time.isoformat()} "
            f"→ {actual}"
        )

        if actual != expected:

            raise RuntimeError(
                "Cycle Mapping失败："
                f"{run_time.isoformat()} "
                f"{actual} != "
                f"{expected}"
            )

    # ======================================================
    # 测试范围保护
    # ======================================================

    try:

        get_ecmwf_model_cycle(
            datetime(
                2024,
                3,
                13,
                18,
                tzinfo=timezone.utc,
            )
        )

    except ValueError:

        print()

        print(
            "[PASS] "
            "2024-03-14之前数据"
            "被正确拒绝"
        )

    else:

        raise RuntimeError(
            "历史起点保护失败"
        )

    print()
    print("=" * 78)

    print(
        "[PASS] ECMWF IFS "
        "Model Cycle Mapping V1"
    )

    print("=" * 78)

    print(
        "48r1 → PASS"
    )

    print(
        "49r1 → PASS"
    )

    print(
        "50r1 → PASS"
    )

    print(
        "Boundary Tests → PASS"
    )


if __name__ == "__main__":

    self_test()