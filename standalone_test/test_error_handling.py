#!/usr/bin/env python3
"""
Error handling and edge case testing for Battery Manager System.

This script tests various error conditions and edge cases to ensure
the system behaves robustly.
"""

import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components" / "battery_manager"))

from battery_manager import BatteryManagerSimulator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TestError(Exception):
    """Custom test error."""
    pass


def test_invalid_configurations():
    """Test invalid configuration values."""
    logger.info("Testing invalid configurations...")
    
    base_config = {
        "battery_capacity_wh": 10000.0,
        "battery_soc_min_percent": 10.0,
        "battery_soc_max_percent": 90.0,
        "battery_charge_efficiency": 0.95,
        "battery_discharge_efficiency": 0.95,
        "pv_peak_power_w": 8000.0,
        "ac_base_load_w": 300.0,
        "dc_base_load_w": 50.0,
        "charger_efficiency": 0.93,
        "inverter_efficiency": 0.93,
        "controller_max_threshold_percent": 80.0,
    }
    
    test_cases = [
        # Invalid battery capacity
        {
            "name": "Zero battery capacity",
            "config": {**base_config, "battery_capacity_wh": 0.0},
            "should_fail": True,
        },
        {
            "name": "Negative battery capacity",
            "config": {**base_config, "battery_capacity_wh": -1000.0},
            "should_fail": True,
        },
        # Invalid SOC limits
        {
            "name": "Min SOC > Max SOC",
            "config": {**base_config, "battery_soc_min_percent": 80.0, "battery_soc_max_percent": 20.0},
            "should_fail": True,
        },
        {
            "name": "Negative Min SOC",
            "config": {**base_config, "battery_soc_min_percent": -10.0},
            "should_fail": True,
        },
        {
            "name": "SOC > 100%",
            "config": {**base_config, "battery_soc_max_percent": 110.0},
            "should_fail": True,
        },
        # Invalid efficiency values
        {
            "name": "Zero efficiency",
            "config": {**base_config, "battery_charge_efficiency": 0.0},
            "should_fail": True,
        },
        {
            "name": "Efficiency > 1.0",
            "config": {**base_config, "battery_discharge_efficiency": 1.1},
            "should_fail": True,
        },
        # Invalid power values
        {
            "name": "Negative PV power",
            "config": {**base_config, "pv_peak_power_w": -1000.0},
            "should_fail": True,
        },
        {
            "name": "Negative AC load",
            "config": {**base_config, "ac_base_load_w": -100.0},
            "should_fail": True,
        },
        # Valid edge cases
        {
            "name": "Very small battery",
            "config": {**base_config, "battery_capacity_wh": 100.0},
            "should_fail": False,
        },
        {
            "name": "Very large battery",
            "config": {**base_config, "battery_capacity_wh": 1000000.0},
            "should_fail": False,
        },
        {
            "name": "Narrow SOC range",
            "config": {**base_config, "battery_soc_min_percent": 49.0, "battery_soc_max_percent": 51.0},
            "should_fail": False,
        },
    ]
    
    results = []
    for test_case in test_cases:
        try:
            simulator = BatteryManagerSimulator(test_case["config"])
            result = simulator.run_simulation(50.0, [20.0, 25.0, 18.0], datetime.now())
            
            if test_case["should_fail"]:
                logger.error(f"❌ {test_case['name']}: Should have failed but didn't")
                results.append({"name": test_case["name"], "status": "FAIL", "error": "Should have failed"})
            else:
                logger.info(f"✅ {test_case['name']}: Passed as expected")
                results.append({"name": test_case["name"], "status": "PASS", "result": result})
                
        except Exception as e:
            if test_case["should_fail"]:
                logger.info(f"✅ {test_case['name']}: Failed as expected: {str(e)}")
                results.append({"name": test_case["name"], "status": "PASS", "error": str(e)})
            else:
                logger.error(f"❌ {test_case['name']}: Unexpected failure: {str(e)}")
                results.append({"name": test_case["name"], "status": "FAIL", "error": str(e)})
    
    return results


def test_invalid_inputs():
    """Test invalid input values."""
    logger.info("Testing invalid inputs...")
    
    config = {
        "battery_capacity_wh": 10000.0,
        "battery_soc_min_percent": 10.0,
        "battery_soc_max_percent": 90.0,
        "battery_charge_efficiency": 0.95,
        "battery_discharge_efficiency": 0.95,
        "pv_peak_power_w": 8000.0,
        "ac_base_load_w": 300.0,
        "dc_base_load_w": 50.0,
        "charger_efficiency": 0.93,
        "inverter_efficiency": 0.93,
        "controller_max_threshold_percent": 80.0,
    }
    
    test_cases = [
        # Invalid SOC values
        {
            "name": "Negative SOC",
            "soc": -10.0,
            "forecasts": [20.0, 25.0, 18.0],
            "should_fail": True,
        },
        {
            "name": "SOC > 100%",
            "soc": 110.0,
            "forecasts": [20.0, 25.0, 18.0],
            "should_fail": True,
        },
        # Invalid forecast values
        {
            "name": "Negative forecast",
            "soc": 50.0,
            "forecasts": [-5.0, 25.0, 18.0],
            "should_fail": False,  # Should handle gracefully by clipping to 0
        },
        {
            "name": "Too few forecasts",
            "soc": 50.0,
            "forecasts": [20.0, 25.0],
            "should_fail": True,
        },
        {
            "name": "Too many forecasts",
            "soc": 50.0,
            "forecasts": [20.0, 25.0, 18.0, 15.0],
            "should_fail": True,
        },
        {
            "name": "Empty forecasts",
            "soc": 50.0,
            "forecasts": [],
            "should_fail": True,
        },
        # Edge case valid inputs
        {
            "name": "Zero SOC",
            "soc": 0.0,
            "forecasts": [20.0, 25.0, 18.0],
            "should_fail": False,
        },
        {
            "name": "100% SOC",
            "soc": 100.0,
            "forecasts": [20.0, 25.0, 18.0],
            "should_fail": False,
        },
        {
            "name": "Zero forecasts",
            "soc": 50.0,
            "forecasts": [0.0, 0.0, 0.0],
            "should_fail": False,
        },
        {
            "name": "Very high forecasts",
            "soc": 50.0,
            "forecasts": [1000.0, 1000.0, 1000.0],
            "should_fail": False,
        },
    ]
    
    results = []
    simulator = BatteryManagerSimulator(config)
    
    for test_case in test_cases:
        try:
            result = simulator.run_simulation(
                test_case["soc"], 
                test_case["forecasts"], 
                datetime.now()
            )
            
            if test_case["should_fail"]:
                logger.error(f"❌ {test_case['name']}: Should have failed but didn't")
                results.append({"name": test_case["name"], "status": "FAIL", "error": "Should have failed"})
            else:
                logger.info(f"✅ {test_case['name']}: Passed as expected")
                results.append({"name": test_case["name"], "status": "PASS", "result": result})
                
        except Exception as e:
            if test_case["should_fail"]:
                logger.info(f"✅ {test_case['name']}: Failed as expected: {str(e)}")
                results.append({"name": test_case["name"], "status": "PASS", "error": str(e)})
            else:
                logger.error(f"❌ {test_case['name']}: Unexpected failure: {str(e)}")
                results.append({"name": test_case["name"], "status": "FAIL", "error": str(e)})
    
    return results


def test_extreme_scenarios():
    """Test extreme but valid scenarios."""
    logger.info("Testing extreme scenarios...")
    
    base_config = {
        "battery_capacity_wh": 10000.0,
        "battery_soc_min_percent": 10.0,
        "battery_soc_max_percent": 90.0,
        "battery_charge_efficiency": 0.95,
        "battery_discharge_efficiency": 0.95,
        "pv_peak_power_w": 8000.0,
        "ac_base_load_w": 300.0,
        "dc_base_load_w": 50.0,
        "charger_efficiency": 0.93,
        "inverter_efficiency": 0.93,
        "controller_max_threshold_percent": 80.0,
    }
    
    test_cases = [
        {
            "name": "Extreme low efficiency",
            "config": {**base_config, "battery_charge_efficiency": 0.1, "battery_discharge_efficiency": 0.1},
            "soc": 50.0,
            "forecasts": [20.0, 25.0, 18.0],
        },
        {
            "name": "Tiny battery with huge PV",
            "config": {**base_config, "battery_capacity_wh": 100.0, "pv_peak_power_w": 50000.0},
            "soc": 50.0,
            "forecasts": [200.0, 250.0, 180.0],
        },
        {
            "name": "Huge battery with tiny PV",
            "config": {**base_config, "battery_capacity_wh": 1000000.0, "pv_peak_power_w": 100.0},
            "soc": 50.0,
            "forecasts": [0.5, 0.8, 0.3],
        },
        {
            "name": "Very high loads",
            "config": {**base_config, "ac_base_load_w": 5000.0, "dc_base_load_w": 2000.0},
            "soc": 50.0,
            "forecasts": [20.0, 25.0, 18.0],
        },
        {
            "name": "SOC at exact minimum",
            "config": base_config,
            "soc": 10.0,  # Exact minimum
            "forecasts": [0.0, 0.0, 0.0],
        },
        {
            "name": "SOC at exact maximum",
            "config": base_config,
            "soc": 90.0,  # Exact maximum
            "forecasts": [100.0, 100.0, 100.0],
        },
        {
            "name": "Very narrow SOC range",
            "config": {**base_config, "battery_soc_min_percent": 49.9, "battery_soc_max_percent": 50.1},
            "soc": 50.0,
            "forecasts": [20.0, 25.0, 18.0],
        },
    ]
    
    results = []
    for test_case in test_cases:
        try:
            simulator = BatteryManagerSimulator(test_case["config"])
            result = simulator.run_simulation(
                test_case["soc"], 
                test_case["forecasts"], 
                datetime.now()
            )
            
            # Validate result structure
            required_keys = [
                "soc_threshold_percent", "min_soc_forecast_percent", 
                "max_soc_forecast_percent", "inverter_enabled"
            ]
            for key in required_keys:
                if key not in result:
                    raise TestError(f"Missing required key: {key}")
            
            # Validate result values
            if not (0 <= result["soc_threshold_percent"] <= 100):
                raise TestError(f"Invalid SOC threshold: {result['soc_threshold_percent']}")
            
            if not (0 <= result["min_soc_forecast_percent"] <= 100):
                raise TestError(f"Invalid min SOC forecast: {result['min_soc_forecast_percent']}")
            
            if not (0 <= result["max_soc_forecast_percent"] <= 100):
                raise TestError(f"Invalid max SOC forecast: {result['max_soc_forecast_percent']}")
            
            if result["min_soc_forecast_percent"] > result["max_soc_forecast_percent"]:
                raise TestError("Min SOC forecast > Max SOC forecast")
            
            logger.info(f"✅ {test_case['name']}: Passed")
            results.append({
                "name": test_case["name"], 
                "status": "PASS", 
                "threshold": result["soc_threshold_percent"],
                "inverter_enabled": result["inverter_enabled"]
            })
            
        except Exception as e:
            logger.error(f"❌ {test_case['name']}: {str(e)}")
            results.append({"name": test_case["name"], "status": "FAIL", "error": str(e)})
    
    return results


def test_time_scenarios():
    """Test different time scenarios."""
    logger.info("Testing time scenarios...")
    
    config = {
        "battery_capacity_wh": 10000.0,
        "battery_soc_min_percent": 10.0,
        "battery_soc_max_percent": 90.0,
        "battery_charge_efficiency": 0.95,
        "battery_discharge_efficiency": 0.95,
        "pv_peak_power_w": 8000.0,
        "ac_base_load_w": 300.0,
        "dc_base_load_w": 50.0,
        "charger_efficiency": 0.93,
        "inverter_efficiency": 0.93,
        "controller_max_threshold_percent": 80.0,
    }
    
    base_time = datetime(2025, 6, 8, 12, 0, 0)  # Noon
    
    test_cases = [
        {
            "name": "Early morning (6 AM)",
            "time": base_time.replace(hour=6),
        },
        {
            "name": "Late evening (10 PM)",
            "time": base_time.replace(hour=22),
        },
        {
            "name": "Midnight",
            "time": base_time.replace(hour=0),
        },
        {
            "name": "End of month",
            "time": datetime(2025, 6, 30, 12, 0, 0),
        },
        {
            "name": "Winter solstice",
            "time": datetime(2025, 12, 21, 12, 0, 0),
        },
        {
            "name": "Summer solstice",
            "time": datetime(2025, 6, 21, 12, 0, 0),
        },
    ]
    
    results = []
    simulator = BatteryManagerSimulator(config)
    
    for test_case in test_cases:
        try:
            result = simulator.run_simulation(50.0, [20.0, 25.0, 18.0], test_case["time"])
            
            logger.info(f"✅ {test_case['name']}: Threshold={result['soc_threshold_percent']:.1f}%")
            results.append({
                "name": test_case["name"], 
                "status": "PASS", 
                "threshold": result["soc_threshold_percent"],
                "time": test_case["time"].isoformat()
            })
            
        except Exception as e:
            logger.error(f"❌ {test_case['name']}: {str(e)}")
            results.append({"name": test_case["name"], "status": "FAIL", "error": str(e)})
    
    return results


def print_test_summary(test_name: str, results: List[Dict[str, Any]]):
    """Print summary of test results."""
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = total - passed
    
    print(f"\n{'='*60}")
    print(f"{test_name} SUMMARY")
    print(f"{'='*60}")
    print(f"Total tests: {total}")
    print(f"Passed: {passed} ✅")
    print(f"Failed: {failed} ❌")
    print(f"Success rate: {(passed/total)*100:.1f}%")
    
    if failed > 0:
        print(f"\nFailed tests:")
        for result in results:
            if result["status"] == "FAIL":
                print(f"  - {result['name']}: {result.get('error', 'Unknown error')}")


def main():
    """Main entry point."""
    print("🧪 Battery Manager System - Error Handling & Edge Case Tests")
    print("=" * 80)
    
    all_results = {}
    
    try:
        # Test invalid configurations
        config_results = test_invalid_configurations()
        all_results["config_tests"] = config_results
        print_test_summary("CONFIGURATION TESTS", config_results)
        
        # Test invalid inputs
        input_results = test_invalid_inputs()
        all_results["input_tests"] = input_results
        print_test_summary("INPUT VALIDATION TESTS", input_results)
        
        # Test extreme scenarios
        extreme_results = test_extreme_scenarios()
        all_results["extreme_tests"] = extreme_results
        print_test_summary("EXTREME SCENARIO TESTS", extreme_results)
        
        # Test time scenarios
        time_results = test_time_scenarios()
        all_results["time_tests"] = time_results
        print_test_summary("TIME SCENARIO TESTS", time_results)
        
        # Overall summary
        total_tests = sum(len(results) for results in all_results.values())
        total_passed = sum(
            sum(1 for r in results if r["status"] == "PASS") 
            for results in all_results.values()
        )
        
        print(f"\n{'='*80}")
        print("OVERALL TEST SUMMARY")
        print(f"{'='*80}")
        print(f"Total tests run: {total_tests}")
        print(f"Total passed: {total_passed} ✅")
        print(f"Total failed: {total_tests - total_passed} ❌")
        print(f"Overall success rate: {(total_passed/total_tests)*100:.1f}%")
        
        if total_passed == total_tests:
            print(f"\n🎉 All tests passed! The system handles errors and edge cases properly.")
        else:
            print(f"\n⚠️  Some tests failed. Review the results above.")
        
    except Exception as e:
        logger.error(f"Test suite failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
