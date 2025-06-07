#!/usr/bin/env python3
"""
Complete system validation script for Battery Manager Integration.

This script runs all available tests and provides comprehensive validation
of the entire battery management system.
"""

import sys
import subprocess
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def run_command(command: List[str], description: str) -> Dict[str, Any]:
    """Run a command and return results."""
    logger.info(f"Running: {description}")
    try:
        result = subprocess.run(
            command, 
            capture_output=True, 
            text=True, 
            cwd=Path(__file__).parent,
            timeout=120
        )
        
        return {
            "description": description,
            "command": " ".join(command),
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode
        }
    except subprocess.TimeoutExpired:
        return {
            "description": description,
            "command": " ".join(command),
            "success": False,
            "error": "Command timed out after 120 seconds",
            "returncode": -1
        }
    except Exception as e:
        return {
            "description": description,
            "command": " ".join(command),
            "success": False,
            "error": str(e),
            "returncode": -1
        }


def validate_core_functionality():
    """Validate core functionality."""
    logger.info("🔧 Validating core functionality...")
    
    tests = [
        {
            "command": ["python", "test_battery_manager.py"],
            "description": "Basic core functionality test"
        },
        {
            "command": ["python", "test_battery_manager_cli.py", "--soc", "50", "--forecasts", "20.0,25.0,18.0"],
            "description": "CLI test with default parameters"
        },
        {
            "command": ["python", "test_battery_manager_cli.py", "--soc", "30", "--forecasts", "35.0,40.0,30.0", "--pv-peak-power", "12000"],
            "description": "CLI test with high PV scenario"
        },
        {
            "command": ["python", "test_battery_manager_cli.py", "--run-scenarios"],
            "description": "All predefined scenarios test"
        }
    ]
    
    results = []
    for test in tests:
        result = run_command(test["command"], test["description"])
        results.append(result)
        
        if result["success"]:
            logger.info(f"  ✅ {test['description']}")
        else:
            logger.error(f"  ❌ {test['description']}: {result.get('error', 'Command failed')}")
    
    return results


def validate_error_handling():
    """Validate error handling and edge cases."""
    logger.info("🧪 Validating error handling...")
    
    result = run_command(
        ["python", "test_error_handling.py"],
        "Comprehensive error handling tests"
    )
    
    if result["success"]:
        logger.info("  ✅ Error handling tests passed")
        # Parse the success rate from output
        output = result["stdout"]
        if "Overall success rate: 100.0%" in output:
            logger.info("  ✅ All error handling scenarios passed")
        else:
            logger.warning("  ⚠️  Some error handling tests may have failed")
    else:
        logger.error(f"  ❌ Error handling tests failed: {result.get('error', 'Unknown error')}")
    
    return [result]


def validate_json_export():
    """Validate JSON export functionality."""
    logger.info("📄 Validating JSON export...")
    
    test_file = "validation_test_output.json"
    result = run_command(
        ["python", "test_battery_manager_cli.py", "--soc", "60", "--forecasts", "25.0,30.0,20.0", "--output-json", test_file],
        "JSON export test"
    )
    
    if result["success"]:
        # Check if JSON file was created and is valid
        json_path = Path(__file__).parent / test_file
        if json_path.exists():
            try:
                with open(json_path, 'r') as f:
                    data = json.load(f)
                
                # Validate required fields
                required_fields = [
                    "soc_threshold_percent", "min_soc_forecast_percent", 
                    "max_soc_forecast_percent", "inverter_enabled"
                ]
                
                missing_fields = [field for field in required_fields if field not in data]
                if not missing_fields:
                    logger.info("  ✅ JSON export successful with all required fields")
                else:
                    logger.error(f"  ❌ JSON export missing fields: {missing_fields}")
                    result["success"] = False
                
                # Clean up test file
                json_path.unlink()
                
            except json.JSONDecodeError as e:
                logger.error(f"  ❌ Invalid JSON output: {e}")
                result["success"] = False
            except Exception as e:
                logger.error(f"  ❌ Error reading JSON file: {e}")
                result["success"] = False
        else:
            logger.error("  ❌ JSON file was not created")
            result["success"] = False
    else:
        logger.error(f"  ❌ JSON export test failed: {result.get('error', 'Unknown error')}")
    
    return [result]


def validate_performance():
    """Validate system performance."""
    logger.info("⚡ Validating performance...")
    
    # Test with large data sets
    results = []
    
    # Test with large battery
    result1 = run_command(
        ["python", "test_battery_manager_cli.py", "--battery-capacity", "100000", "--soc", "50", "--forecasts", "50.0,60.0,40.0"],
        "Large battery system test"
    )
    results.append(result1)
    
    # Test with high PV
    result2 = run_command(
        ["python", "test_battery_manager_cli.py", "--pv-peak-power", "50000", "--soc", "30", "--forecasts", "100.0,120.0,80.0"],
        "High PV power test"
    )
    results.append(result2)
    
    # Test with extreme loads
    result3 = run_command(
        ["python", "test_battery_manager_cli.py", "--ac-base-load", "5000", "--dc-base-load", "2000", "--soc", "70", "--forecasts", "30.0,35.0,25.0"],
        "High load test"
    )
    results.append(result3)
    
    success_count = sum(1 for r in results if r["success"])
    if success_count == len(results):
        logger.info(f"  ✅ All {len(results)} performance tests passed")
    else:
        logger.warning(f"  ⚠️  {success_count}/{len(results)} performance tests passed")
    
    return results


def check_file_structure():
    """Check that all required files exist."""
    logger.info("📁 Checking file structure...")
    
    base_path = Path(__file__).parent.parent
    required_files = [
        # Core integration files
        "custom_components/battery_manager/__init__.py",
        "custom_components/battery_manager/config_flow.py",
        "custom_components/battery_manager/const.py",
        "custom_components/battery_manager/coordinator.py",
        "custom_components/battery_manager/manifest.json",
        "custom_components/battery_manager/sensor.py",
        
        # Core logic files
        "custom_components/battery_manager/battery_manager/__init__.py",
        "custom_components/battery_manager/battery_manager/battery.py",
        "custom_components/battery_manager/battery_manager/charger.py",
        "custom_components/battery_manager/battery_manager/consumers.py",
        "custom_components/battery_manager/battery_manager/controller.py",
        "custom_components/battery_manager/battery_manager/energy_flow.py",
        "custom_components/battery_manager/battery_manager/inverter.py",
        "custom_components/battery_manager/battery_manager/pv_system.py",
        "custom_components/battery_manager/battery_manager/simulator.py",
        
        # Translation files
        "custom_components/battery_manager/translations/en.json",
        
        # Test files
        "standalone_test/test_battery_manager.py",
        "standalone_test/test_battery_manager_cli.py",
        "standalone_test/test_error_handling.py",
        
        # Documentation
        "README.md"
    ]
    
    missing_files = []
    for file_path in required_files:
        full_path = base_path / file_path
        if not full_path.exists():
            missing_files.append(file_path)
    
    if not missing_files:
        logger.info(f"  ✅ All {len(required_files)} required files found")
        return True
    else:
        logger.error(f"  ❌ Missing {len(missing_files)} files:")
        for file_path in missing_files:
            logger.error(f"    - {file_path}")
        return False


def generate_validation_report(all_results: Dict[str, List[Dict[str, Any]]]):
    """Generate comprehensive validation report."""
    logger.info("📊 Generating validation report...")
    
    report = {
        "validation_time": datetime.now().isoformat(),
        "summary": {},
        "details": all_results
    }
    
    # Calculate summary statistics
    total_tests = sum(len(results) for results in all_results.values())
    total_passed = sum(
        sum(1 for result in results if result["success"]) 
        for results in all_results.values()
    )
    
    report["summary"] = {
        "total_test_categories": len(all_results),
        "total_tests": total_tests,
        "total_passed": total_passed,
        "total_failed": total_tests - total_passed,
        "success_rate": (total_passed / total_tests * 100) if total_tests > 0 else 0,
        "overall_status": "PASS" if total_passed == total_tests else "FAIL"
    }
    
    # Save report
    report_path = Path(__file__).parent / "validation_report.json"
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    
    logger.info(f"  ✅ Validation report saved to: {report_path}")
    
    return report


def print_final_summary(report: Dict[str, Any]):
    """Print final validation summary."""
    summary = report["summary"]
    
    print("\n" + "="*80)
    print("🏁 BATTERY MANAGER INTEGRATION - VALIDATION SUMMARY")
    print("="*80)
    
    print(f"\n📊 STATISTICS:")
    print(f"  Test Categories: {summary['total_test_categories']}")
    print(f"  Total Tests: {summary['total_tests']}")
    print(f"  Tests Passed: {summary['total_passed']} ✅")
    print(f"  Tests Failed: {summary['total_failed']} ❌")
    print(f"  Success Rate: {summary['success_rate']:.1f}%")
    
    print(f"\n🎯 OVERALL STATUS: {summary['overall_status']}")
    
    if summary["overall_status"] == "PASS":
        print(f"\n🎉 ALL VALIDATIONS PASSED!")
        print(f"   The Battery Manager Integration is ready for use.")
        print(f"   • Core functionality works correctly")
        print(f"   • Error handling is robust") 
        print(f"   • Performance is acceptable")
        print(f"   • All files are present")
    else:
        print(f"\n⚠️  SOME VALIDATIONS FAILED!")
        print(f"   Please review the test results above and fix any issues.")
        
        # Show failed categories
        for category, results in report["details"].items():
            failed_tests = [r for r in results if not r["success"]]
            if failed_tests:
                print(f"   • {category}: {len(failed_tests)} test(s) failed")
    
    print(f"\n📄 Detailed report: validation_report.json")
    print("="*80)


def main():
    """Main validation entry point."""
    print("🔍 Battery Manager Integration - Complete System Validation")
    print("="*80)
    
    start_time = datetime.now()
    all_results = {}
    
    # Check file structure first
    logger.info("Starting validation process...")
    file_structure_ok = check_file_structure()
    
    if not file_structure_ok:
        logger.error("❌ File structure validation failed. Cannot proceed with other tests.")
        sys.exit(1)
    
    # Run all validation tests
    try:
        all_results["core_functionality"] = validate_core_functionality()
        all_results["error_handling"] = validate_error_handling()
        all_results["json_export"] = validate_json_export()
        all_results["performance"] = validate_performance()
        
        # Generate final report
        report = generate_validation_report(all_results)
        
        # Print summary
        print_final_summary(report)
        
        # Set exit code based on results
        if report["summary"]["overall_status"] == "PASS":
            logger.info(f"✅ Validation completed successfully in {datetime.now() - start_time}")
            sys.exit(0)
        else:
            logger.error(f"❌ Validation completed with failures in {datetime.now() - start_time}")
            sys.exit(1)
            
    except KeyboardInterrupt:
        logger.error("❌ Validation interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Validation failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
