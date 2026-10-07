import sys
import unittest

from src.parsers.zuuu_metar_parser import (
    parse_temperature,
    parse_wind,
    parse_cloud,
    is_weather_token,
    split_metar_sections,
    parse_metar,
)


class TestTemperature(unittest.TestCase):

    def test_positive(self):
        self.assertEqual(parse_temperature("24"), 24)
        self.assertEqual(parse_temperature("05"), 5)

    def test_negative(self):
        self.assertEqual(parse_temperature("M02"), -2)
        self.assertEqual(parse_temperature("M15"), -15)

    def test_missing(self):
        self.assertIsNone(parse_temperature("//"))
        self.assertIsNone(parse_temperature(None))


class TestWind(unittest.TestCase):

    def test_normal_mps(self):
        result = parse_wind("04007MPS")

        self.assertEqual(
            result["wind_direction_deg"],
            40,
        )
        self.assertEqual(
            result["wind_speed_mps"],
            7,
        )
        self.assertIsNone(
            result["wind_gust_mps"]
        )
        self.assertFalse(
            result["variable_wind"]
        )

    def test_gust(self):
        result = parse_wind(
            "04007G12MPS"
        )

        self.assertEqual(
            result["wind_direction_deg"],
            40,
        )
        self.assertEqual(
            result["wind_speed_mps"],
            7,
        )
        self.assertEqual(
            result["wind_gust_mps"],
            12,
        )

    def test_variable_wind(self):
        result = parse_wind(
            "VRB03MPS"
        )

        self.assertIsNone(
            result["wind_direction_deg"]
        )
        self.assertEqual(
            result["wind_speed_mps"],
            3,
        )
        self.assertTrue(
            result["variable_wind"]
        )

    def test_calm(self):
        result = parse_wind(
            "00000MPS"
        )

        self.assertEqual(
            result["wind_direction_deg"],
            0,
        )
        self.assertEqual(
            result["wind_speed_mps"],
            0,
        )

    def test_knots_conversion(self):
        result = parse_wind(
            "09010KT"
        )

        self.assertAlmostEqual(
            result["wind_speed_mps"],
            5.144,
            places=3,
        )

    def test_invalid(self):
        self.assertIsNone(
            parse_wind("HELLO")
        )


class TestCloud(unittest.TestCase):

    def test_normal_cloud(self):
        result = parse_cloud(
            "FEW026"
        )

        self.assertEqual(
            result,
            {
                "cover": "FEW",
                "base_ft": 2600,
                "cloud_type": None,
            },
        )

    def test_cb(self):
        result = parse_cloud(
            "BKN020CB"
        )

        self.assertEqual(
            result["cover"],
            "BKN",
        )
        self.assertEqual(
            result["base_ft"],
            2000,
        )
        self.assertEqual(
            result["cloud_type"],
            "CB",
        )

    def test_tcu(self):
        result = parse_cloud(
            "SCT030TCU"
        )

        self.assertEqual(
            result["cloud_type"],
            "TCU",
        )

    def test_unknown_height(self):
        result = parse_cloud(
            "BKN///"
        )

        self.assertIsNone(
            result["base_ft"]
        )

    def test_invalid(self):
        self.assertIsNone(
            parse_cloud("ABC123")
        )


class TestWeather(unittest.TestCase):

    def test_weather_codes(self):
        valid = [
            "RA",
            "-RA",
            "+TSRA",
            "BR",
            "FG",
            "SHRA",
            "SN",
            "HZ",
        ]

        for token in valid:
            with self.subTest(
                token=token
            ):
                self.assertTrue(
                    is_weather_token(
                        token
                    )
                )

    def test_non_weather(self):
        invalid = [
            "ZUUU",
            "9999",
            "FEW026",
            "24/19",
            "Q1011",
        ]

        for token in invalid:
            with self.subTest(
                token=token
            ):
                self.assertFalse(
                    is_weather_token(
                        token
                    )
                )


class TestSectionIsolation(unittest.TestCase):

    def test_tempo_split(self):
        raw = (
            "METAR ZUUU 280400Z "
            "04007MPS 9999 "
            "FEW026 24/19 Q1011 "
            "TEMPO 18015MPS "
            "2000 TSRA BKN010CB"
        )

        observation, trend, remarks = (
            split_metar_sections(raw)
        )

        self.assertEqual(
            observation,
            [
                "METAR",
                "ZUUU",
                "280400Z",
                "04007MPS",
                "9999",
                "FEW026",
                "24/19",
                "Q1011",
            ],
        )

        self.assertEqual(
            trend,
            (
                "TEMPO 18015MPS "
                "2000 TSRA BKN010CB"
            ),
        )

        self.assertIsNone(remarks)

    def test_becmg_split(self):
        raw = (
            "METAR ZUUU 280400Z "
            "04007MPS 9999 "
            "FEW026 24/19 Q1011 "
            "BECMG 18010MPS 4000"
        )

        observation, trend, remarks = (
            split_metar_sections(raw)
        )

        self.assertNotIn(
            "BECMG",
            observation,
        )

        self.assertEqual(
            trend,
            "BECMG 18010MPS 4000",
        )

        self.assertIsNone(remarks)

    def test_nosig_split(self):
        raw = (
            "METAR ZUUU 280400Z "
            "04007MPS 9999 "
            "FEW026 24/19 Q1011 "
            "NOSIG"
        )

        observation, trend, remarks = (
            split_metar_sections(raw)
        )

        self.assertNotIn(
            "NOSIG",
            observation,
        )

        self.assertEqual(
            trend,
            "NOSIG",
        )

        self.assertIsNone(remarks)

    def test_rmk_split(self):
        raw = (
            "METAR ZUUU 280400Z "
            "04007MPS 9999 "
            "FEW026 24/19 Q1011 "
            "RMK TEST REMARK"
        )

        observation, trend, remarks = (
            split_metar_sections(raw)
        )

        self.assertNotIn(
            "RMK",
            observation,
        )

        self.assertIsNone(trend)

        self.assertEqual(
            remarks,
            "TEST REMARK",
        )

    def test_trend_and_rmk(self):
        raw = (
            "METAR ZUUU 280400Z "
            "04007MPS 9999 "
            "FEW026 24/19 Q1011 "
            "TEMPO 18015MPS "
            "2000 TSRA BKN010CB "
            "RMK TEST"
        )

        observation, trend, remarks = (
            split_metar_sections(raw)
        )

        self.assertEqual(
            trend,
            (
                "TEMPO 18015MPS "
                "2000 TSRA BKN010CB"
            ),
        )

        self.assertEqual(
            remarks,
            "TEST",
        )


class TestMetarParser(unittest.TestCase):

    def test_normal_zuuu(self):
        raw = (
            "METAR ZUUU 280400Z "
            "04007MPS "
            "9999 "
            "FEW026 "
            "24/19 "
            "Q1011 "
            "NOSIG"
        )

        result = parse_metar(raw)

        self.assertEqual(
            result["station_id"],
            "ZUUU",
        )

        self.assertEqual(
            result["report_type"],
            "METAR",
        )

        self.assertEqual(
            result["metar_time_group"],
            "280400Z",
        )

        self.assertEqual(
            result["temperature_c"],
            24,
        )

        self.assertEqual(
            result["dewpoint_c"],
            19,
        )

        self.assertEqual(
            result["wind_direction_deg"],
            40,
        )

        self.assertEqual(
            result["wind_speed_mps"],
            7,
        )

        self.assertEqual(
            result["visibility_m"],
            10000,
        )

        self.assertEqual(
            result["qnh_hpa"],
            1011,
        )

        self.assertEqual(
            result["trend"],
            "NOSIG",
        )

        self.assertEqual(
            result["cloud_layers"],
            [
                {
                    "cover": "FEW",
                    "base_ft": 2600,
                    "cloud_type": None,
                }
            ],
        )

    def test_tempo_does_not_pollute_observation(self):
        raw = (
            "METAR ZUUU 280400Z "
            "04007MPS "
            "9999 "
            "FEW026 "
            "24/19 "
            "Q1011 "
            "TEMPO "
            "18015MPS "
            "2000 "
            "TSRA "
            "BKN010CB"
        )

        result = parse_metar(raw)

        # 当前观测必须保持原值
        self.assertEqual(
            result["wind_direction_deg"],
            40,
        )

        self.assertEqual(
            result["wind_speed_mps"],
            7,
        )

        self.assertEqual(
            result["visibility_m"],
            10000,
        )

        self.assertEqual(
            result["temperature_c"],
            24,
        )

        self.assertEqual(
            result["dewpoint_c"],
            19,
        )

        self.assertEqual(
            result["qnh_hpa"],
            1011,
        )

        self.assertEqual(
            result["weather_phenomena"],
            [],
        )

        self.assertEqual(
            result["cloud_layers"],
            [
                {
                    "cover": "FEW",
                    "base_ft": 2600,
                    "cloud_type": None,
                }
            ],
        )

        # 趋势必须单独保存
        self.assertEqual(
            result["trend"],
            (
                "TEMPO 18015MPS "
                "2000 TSRA BKN010CB"
            ),
        )

    def test_becmg_does_not_pollute_observation(self):
        raw = (
            "METAR ZUUU 280400Z "
            "04007MPS "
            "9999 "
            "FEW026 "
            "24/19 "
            "Q1011 "
            "BECMG "
            "22018MPS "
            "3000 "
            "+RA "
            "OVC008"
        )

        result = parse_metar(raw)

        self.assertEqual(
            result["wind_direction_deg"],
            40,
        )

        self.assertEqual(
            result["wind_speed_mps"],
            7,
        )

        self.assertEqual(
            result["visibility_m"],
            10000,
        )

        self.assertEqual(
            result["weather_phenomena"],
            [],
        )

        self.assertEqual(
            result["cloud_layers"][0][
                "base_ft"
            ],
            2600,
        )

        self.assertEqual(
            result["trend"],
            (
                "BECMG 22018MPS "
                "3000 +RA OVC008"
            ),
        )

    def test_rmk_does_not_pollute_observation(self):
        raw = (
            "METAR ZUUU 280400Z "
            "04007MPS "
            "9999 "
            "FEW026 "
            "24/19 "
            "Q1011 "
            "RMK "
            "18020MPS "
            "1000 "
            "TSRA "
            "BKN005"
        )

        result = parse_metar(raw)

        self.assertEqual(
            result["wind_direction_deg"],
            40,
        )

        self.assertEqual(
            result["wind_speed_mps"],
            7,
        )

        self.assertEqual(
            result["visibility_m"],
            10000,
        )

        self.assertEqual(
            result["weather_phenomena"],
            [],
        )

        self.assertEqual(
            len(
                result["cloud_layers"]
            ),
            1,
        )

        self.assertIsNone(
            result["trend"]
        )

        self.assertEqual(
            result["remarks"],
            (
                "18020MPS 1000 "
                "TSRA BKN005"
            ),
        )

    def test_current_weather_preserved(self):
        raw = (
            "METAR ZUUU 280400Z "
            "04007MPS "
            "5000 "
            "-RA "
            "SCT020 "
            "BKN040 "
            "22/20 "
            "Q1008 "
            "TEMPO "
            "1000 "
            "TSRA "
            "BKN008CB"
        )

        result = parse_metar(raw)

        self.assertEqual(
            result["visibility_m"],
            5000,
        )

        self.assertEqual(
            result["weather_phenomena"],
            ["-RA"],
        )

        self.assertEqual(
            len(
                result["cloud_layers"]
            ),
            2,
        )

        self.assertEqual(
            result["cloud_layers"][0][
                "cover"
            ],
            "SCT",
        )

        self.assertEqual(
            result["cloud_layers"][1][
                "cover"
            ],
            "BKN",
        )

        self.assertNotIn(
            "TSRA",
            result["weather_phenomena"],
        )

    def test_speci(self):
        raw = (
            "SPECI ZUUU 281015Z "
            "VRB03MPS "
            "3000 "
            "TSRA "
            "BKN015CB "
            "21/19 "
            "Q1007"
        )

        result = parse_metar(raw)

        self.assertEqual(
            result["report_type"],
            "SPECI",
        )

        self.assertTrue(
            result["variable_wind"]
        )

        self.assertEqual(
            result["visibility_m"],
            3000,
        )

        self.assertEqual(
            result["weather_phenomena"],
            ["TSRA"],
        )

    def test_corrected_report(self):
        raw = (
            "METAR ZUUU 280400Z COR "
            "04007MPS 9999 "
            "FEW026 24/19 Q1011"
        )

        result = parse_metar(raw)

        self.assertTrue(
            result["is_corrected"]
        )

    def test_auto_report(self):
        raw = (
            "METAR ZUUU 280400Z AUTO "
            "04007MPS 9999 "
            "FEW026 24/19 Q1011"
        )

        result = parse_metar(raw)

        self.assertTrue(
            result["is_auto"]
        )

    def test_variable_direction_range(self):
        raw = (
            "METAR ZUUU 280400Z "
            "04007MPS "
            "020V090 "
            "9999 "
            "FEW026 "
            "24/19 "
            "Q1011"
        )

        result = parse_metar(raw)

        self.assertTrue(
            result["variable_wind"]
        )

        self.assertEqual(
            result["variable_wind_from"],
            20,
        )

        self.assertEqual(
            result["variable_wind_to"],
            90,
        )

    def test_cavok(self):
        raw = (
            "METAR ZUUU 280400Z "
            "04007MPS "
            "CAVOK "
            "24/19 "
            "Q1011"
        )

        result = parse_metar(raw)

        self.assertTrue(
            result["cavok"]
        )

        self.assertEqual(
            result["visibility_m"],
            10000,
        )

    def test_negative_temperature(self):
        raw = (
            "METAR ZUUU 280400Z "
            "00000MPS "
            "9999 "
            "NSC "
            "M02/M05 "
            "Q1025"
        )

        result = parse_metar(raw)

        self.assertEqual(
            result["temperature_c"],
            -2,
        )

        self.assertEqual(
            result["dewpoint_c"],
            -5,
        )

    def test_missing_temperature(self):
        raw = (
            "METAR ZUUU 280400Z "
            "00000MPS "
            "9999 "
            "NSC "
            "///// "
            "Q1025"
        )

        result = parse_metar(raw)

        self.assertIsNone(
            result["temperature_c"]
        )

        self.assertIsNone(
            result["dewpoint_c"]
        )

    def test_empty_report_fails(self):
        with self.assertRaises(
            ValueError
        ):
            parse_metar("")

    def test_missing_station_fails(self):
        with self.assertRaises(
            ValueError
        ):
            parse_metar(
                "METAR 280400Z "
                "04007MPS 9999 "
                "24/19 Q1011"
            )

    def test_missing_time_fails(self):
        with self.assertRaises(
            ValueError
        ):
            parse_metar(
                "METAR ZUUU "
                "04007MPS 9999 "
                "24/19 Q1011"
            )


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(
        sys.modules[__name__]
    )

    runner = unittest.TextTestRunner(
        verbosity=2
    )

    result = runner.run(
        suite
    )

    sys.exit(
        0
        if result.wasSuccessful()
        else 1
    )