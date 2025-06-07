#!/usr/bin/env python3
"""
Comprehensive standalone test script for Battery Manager System with CLI parameters.

This script allows testing the battery management system with various scenarios
and configurations via command line arguments.
"""

import argparse
import json
import sys
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components" / "battery_manager"))

from battery_manager import BatteryManagerSimulator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Test Battery Manager System with various configurations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic test with default values
  python test_battery_manager_cli.py

  # Test with custom SOC and forecasts
  python test_battery_manager_cli.py --soc 45 --forecasts 15.5,18.2,12.8

  # Test with larger battery system
  python test_battery_manager_cli.py --battery-capacity 20000 --battery-soc-min 15 --battery-soc-max 95

  # Test with high PV production scenario
  python test_battery_manager_cli.py --soc 30 --forecasts 35.0,40.0,30.0 --pv-peak-power 12000

  # Run all predefined test scenarios
  python test_battery_manager_cli.py --run-scenarios

  # Verbose output with debug information
  python test_battery_manager_cli.py --verbose --soc 60 --forecasts 20.0,25.0,15.0
        """
    )

    # Input parameters
    parser.add_argument(
        "--soc", type=float, default=50.0,
        help="Current State of Charge in percent (0-100). Default: 50.0"
    )
    parser.add_argument(
        "--forecasts", type=str, default="20.0,25.0,18.0",
        help="Daily PV forecasts in kWh for today,tomorrow,day_after (comma-separated). Default: '20.0,25.0,18.0'"
    )
    parser.add_argument(
        "--current-time", type=str, default=None,
        help="Current time in ISO format (YYYY-MM-DDTHH:MM:SS). Default: current time"
    )

    # Battery configuration
    parser.add_argument(
        "--battery-capacity", type=float, default=10000.0,
        help="Battery capacity in Wh. Default: 10000.0"
    )
    parser.add_argument(
        "--battery-soc-min", type=float, default=10.0,
        help="Minimum SOC in percent. Default: 10.0"
    )
    parser.add_argument(
        "--battery-soc-max", type=float, default=90.0,
        help="Maximum SOC in percent. Default: 90.0"
    )
    parser.add_argument(
        "--battery-charge-efficiency", type=float, default=0.95,
        help="Battery charge efficiency (0-1). Default: 0.95"
    )
    parser.add_argument(
        "--battery-discharge-efficiency", type=float, default=0.95,
        help="Battery discharge efficiency (0-1). Default: 0.95"
    )

    # PV system configuration
    parser.add_argument(
        "--pv-peak-power", type=float, default=8000.0,
        help="PV peak power in W. Default: 8000.0"
    )

    # Consumer configuration
    parser.add_argument(
        "--ac-base-load", type=float, default=300.0,
        help="AC base load in W. Default: 300.0"
    )
    parser.add_argument(
        "--dc-base-load", type=float, default=50.0,
        help="DC base load in W. Default: 50.0"
    )

    # Charger/Inverter configuration
    parser.add_argument(
        "--charger-efficiency", type=float, default=0.93,
        help="Charger efficiency (0-1). Default: 0.93"
    )
    parser.add_argument(
        "--inverter-efficiency", type=float, default=0.93,
        help="Inverter efficiency (0-1). Default: 0.93"
    )

    # Controller configuration
    parser.add_argument(
        "--controller-max-threshold", type=float, default=80.0,
        help="Controller maximum threshold in percent. Default: 80.0"
    )

    # Test options
    parser.add_argument(
        "--run-scenarios", action="store_true",
        help="Run predefined test scenarios instead of single test"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose output with detailed component information"
    )
    parser.add_argument(
        "--output-json", type=str, default=None,
        help="Save results to JSON file"
    )

    return parser.parse_args()


def parse_forecasts(forecast_str: str) -> List[float]:
    """Parse forecast string into list of floats."""
    try:
        forecasts = [float(x.strip()) for x in forecast_str.split(",")]
        if len(forecasts) != 3:
            raise ValueError("Exactly 3 forecast values required")
        if any(f < 0 for f in forecasts):
            raise ValueError("Forecast values must be non-negative")
        return forecasts
    except ValueError as e:
        logger.error(f"Invalid forecast format: {e}")
        sys.exit(1)


def parse_current_time(time_str: str) -> datetime:
    """Parse current time string."""
    try:
        if time_str:
            return datetime.fromisoformat(time_str)
        else:
            return datetime.now()
    except ValueError as e:
        logger.error(f"Invalid time format: {e}")
        sys.exit(1)


def build_config(args: argparse.Namespace) -> Dict[str, Any]:
    """Build configuration dictionary from arguments."""
    return {
        "battery_capacity_wh": args.battery_capacity,
        "battery_soc_min_percent": args.battery_soc_min,
        "battery_soc_max_percent": args.battery_soc_max,
        "battery_charge_efficiency": args.battery_charge_efficiency,
        "battery_discharge_efficiency": args.battery_discharge_efficiency,
        "pv_peak_power_w": args.pv_peak_power,
        "ac_base_load_w": args.ac_base_load,
        "dc_base_load_w": args.dc_base_load,
        "charger_efficiency": args.charger_efficiency,
        "inverter_efficiency": args.inverter_efficiency,
        "controller_max_threshold_percent": args.controller_max_threshold,
    }


def run_single_test(args: argparse.Namespace) -> Dict[str, Any]:
    """Run a single test with the provided arguments."""
    logger.info("Running single test scenario")
    
    # Parse inputs
    current_soc = args.soc
    daily_forecasts = parse_forecasts(args.forecasts)
    current_time = parse_current_time(args.current_time)
    config = build_config(args)
    
    # Validate inputs
    if not (0 <= current_soc <= 100):
        logger.error(f"SOC must be between 0 and 100, got: {current_soc}")
        sys.exit(1)
    
    # Run simulation
    simulator = BatteryManagerSimulator(config)
    results = simulator.run_simulation(current_soc, daily_forecasts, current_time)
    
    # Add test metadata
    results["test_metadata"] = {
        "test_type": "single",
        "input_soc": current_soc,
        "input_forecasts": daily_forecasts,
        "current_time": current_time.isoformat(),
        "config": config,
    }
    
    return results


def get_predefined_scenarios() -> List[Dict[str, Any]]:
    """Get predefined test scenarios."""
    return [
        {
            "name": "Basic Operation",
            "description": "Normal operation with moderate SOC and forecasts",
            "soc": 50.0,
            "forecasts": [20.0, 25.0, 18.0],
            "config_overrides": {},
        },
        {
            "name": "Low SOC Critical",
            "description": "Low SOC requiring immediate charging",
            "soc": 15.0,
            "forecasts": [20.0, 25.0, 18.0],
            "config_overrides": {},
        },
        {
            "name": "High SOC with Low PV",
            "description": "High SOC with poor weather forecast",
            "soc": 85.0,
            "forecasts": [5.0, 8.0, 3.0],
            "config_overrides": {},
        },
        {
            "name": "High PV Production",
            "description": "Excellent weather with high PV forecast",
            "soc": 30.0,
            "forecasts": [35.0, 40.0, 30.0],
            "config_overrides": {"pv_peak_power_w": 12000.0},
        },
        {
            "name": "Large Battery System",
            "description": "Test with larger battery capacity",
            "soc": 45.0,
            "forecasts": [25.0, 30.0, 20.0],
            "config_overrides": {
                "battery_capacity_wh": 20000.0,
                "pv_peak_power_w": 10000.0,
            },
        },
        {
            "name": "Conservative Settings",
            "description": "Conservative SOC limits and efficiency",
            "soc": 60.0,
            "forecasts": [18.0, 22.0, 15.0],
            "config_overrides": {
                "battery_soc_min_percent": 20.0,
                "battery_soc_max_percent": 80.0,
                "battery_charge_efficiency": 0.90,
                "battery_discharge_efficiency": 0.90,
            },
        },
        {
            "name": "Edge Case - Zero Forecast",
            "description": "Test with zero PV forecast",
            "soc": 70.0,
            "forecasts": [0.0, 0.0, 0.0],
            "config_overrides": {},
        },
        {
            "name": "Edge Case - Maximum SOC",
            "description": "Test at maximum SOC",
            "soc": 90.0,
            "forecasts": [20.0, 25.0, 18.0],
            "config_overrides": {},
        },
        {
            "name": "Edge Case - Minimum SOC",
            "description": "Test at minimum SOC",
            "soc": 10.0,
            "forecasts": [20.0, 25.0, 18.0],
            "config_overrides": {},
        },
    ]


def run_scenarios(args: argparse.Namespace) -> List[Dict[str, Any]]:
    """Run all predefined test scenarios."""
    logger.info("Running predefined test scenarios")
    
    scenarios = get_predefined_scenarios()
    results = []
    base_config = build_config(args)
    current_time = parse_current_time(args.current_time)
    
    for i, scenario in enumerate(scenarios, 1):
        logger.info(f"Running scenario {i}/{len(scenarios)}: {scenario['name']}")
        
        # Build scenario config
        config = {**base_config, **scenario["config_overrides"]}
        
        # Run simulation
        simulator = BatteryManagerSimulator(config)
        result = simulator.run_simulation(
            scenario["soc"], 
            scenario["forecasts"], 
            current_time
        )
        
        # Add scenario metadata
        result["test_metadata"] = {
            "test_type": "scenario",
            "scenario_name": scenario["name"],
            "scenario_description": scenario["description"],
            "input_soc": scenario["soc"],
            "input_forecasts": scenario["forecasts"],
            "current_time": current_time.isoformat(),
            "config": config,
        }
        
        results.append(result)
        
        if args.verbose:
            print_scenario_result(scenario, result)
    
    return results


def print_single_result(result: Dict[str, Any], verbose: bool = False) -> None:
    """Print results of a single test."""
    metadata = result["test_metadata"]
    
    print("\n" + "="*80)
    print("BATTERY MANAGER TEST RESULTS")
    print("="*80)
    
    print(f"\n📊 INPUT:")
    print(f"  Current SOC: {metadata['input_soc']:.1f}%")
    print(f"  PV Forecasts: {metadata['input_forecasts']} kWh")
    print(f"  Test Time: {metadata['current_time']}")
    
    print(f"\n🎯 RESULTS:")
    print(f"  SOC Threshold: {result['soc_threshold_percent']:.1f}%")
    print(f"  Inverter Enabled: {'✅ YES' if result['inverter_enabled'] else '❌ NO'}")
    print(f"  Min SOC Forecast: {result['min_soc_forecast_percent']:.1f}%")
    print(f"  Max SOC Forecast: {result['max_soc_forecast_percent']:.1f}%")
    
    if verbose:
        print(f"\n🔧 CONFIGURATION:")
        config = metadata['config']
        print(f"  Battery Capacity: {config['battery_capacity_wh']:.0f} Wh")
        print(f"  Battery SOC Range: {config['battery_soc_min_percent']:.1f}% - {config['battery_soc_max_percent']:.1f}%")
        print(f"  PV Peak Power: {config['pv_peak_power_w']:.0f} W")
        print(f"  AC Base Load: {config['ac_base_load_w']:.0f} W")
        print(f"  DC Base Load: {config['dc_base_load_w']:.0f} W")
        
        if "energy_flows" in result:
            print(f"\n⚡ ENERGY FLOWS:")
            flows = result["energy_flows"]
            for hour, flow in enumerate(flows[:6]):  # Show first 6 hours
                print(f"  Hour {hour:2d}: Grid={flow['grid_import_export_w']:+6.0f}W, "
                      f"Batt={flow['battery_charge_discharge_w']:+6.0f}W, "
                      f"SOC={flow['battery_soc_percent']:5.1f}%")


def print_scenario_result(scenario: Dict[str, Any], result: Dict[str, Any]) -> None:
    """Print results of a scenario test."""
    print(f"\n📋 {scenario['name']}")
    print(f"   {scenario['description']}")
    print(f"   SOC: {scenario['soc']:.1f}% → Threshold: {result['soc_threshold_percent']:.1f}%")
    print(f"   Inverter: {'✅ ON' if result['inverter_enabled'] else '❌ OFF'}")
    print(f"   SOC Range: {result['min_soc_forecast_percent']:.1f}% - {result['max_soc_forecast_percent']:.1f}%")


def print_scenarios_summary(results: List[Dict[str, Any]]) -> None:
    """Print summary of all scenario results."""
    print("\n" + "="*80)
    print("SCENARIO TEST RESULTS SUMMARY")
    print("="*80)
    
    for result in results:
        scenario_name = result["test_metadata"]["scenario_name"]
        print_scenario_result(
            {
                "name": scenario_name,
                "description": result["test_metadata"]["scenario_description"],
                "soc": result["test_metadata"]["input_soc"],
            },
            result
        )
    
    print("\n📈 STATISTICS:")
    thresholds = [r["soc_threshold_percent"] for r in results]
    inverter_on_count = sum(1 for r in results if r["inverter_enabled"])
    
    print(f"  Average SOC Threshold: {sum(thresholds)/len(thresholds):.1f}%")
    print(f"  Min SOC Threshold: {min(thresholds):.1f}%")
    print(f"  Max SOC Threshold: {max(thresholds):.1f}%")
    print(f"  Inverter Enabled: {inverter_on_count}/{len(results)} scenarios")


def save_results_to_json(results: Any, filepath: str) -> None:
    """Save results to JSON file."""
    try:
        with open(filepath, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        logger.info(f"Results saved to: {filepath}")
    except Exception as e:
        logger.error(f"Failed to save results to {filepath}: {e}")


def main():
    """Main entry point."""
    args = parse_arguments()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        if args.run_scenarios:
            results = run_scenarios(args)
            print_scenarios_summary(results)
        else:
            results = run_single_test(args)
            print_single_result(results, args.verbose)
        
        if args.output_json:
            save_results_to_json(results, args.output_json)
        
        print(f"\n✅ Test completed successfully!")
        
    except KeyboardInterrupt:
        print("\n❌ Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Test failed: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
