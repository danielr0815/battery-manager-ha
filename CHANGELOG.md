# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/0.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2025-06-08

### Added
- Initial release of Battery Manager Home Assistant Integration
- Intelligent SOC threshold calculation based on PV forecasts
- Real-time energy flow simulation with battery, PV, and load modeling
- Home Assistant UI configuration via config flow
- Four main sensor entities:
  - Binary sensor for inverter status (on/off)
  - Sensor for calculated SOC threshold percentage
  - Sensor for minimum forecasted SOC
  - Sensor for maximum forecasted SOC
- Comprehensive standalone testing suite
- Robust error handling and input validation
- Flexible configuration options for different battery and PV system sizes
- Automatic entity monitoring with debounced updates
- Device grouping for all entities
- HACS custom repository support
- GitHub Actions workflows for validation
- Comprehensive documentation and installation guides

### Features
- **PV System Modeling**: Configurable PV system with hourly production distribution
- **Battery Simulation**: Charge/discharge efficiency modeling with SOC constraints
- **Load Profiles**: Separate AC and DC consumption modeling
- **Energy Flow Calculator**: Complex multi-component energy flow simulation
- **Maximum-Based Controller**: Intelligent threshold calculation algorithm
- **Multi-language Support**: English translations included
- **Performance Optimized**: Efficient algorithms suitable for real-time operation

### Technical Details
- Compatible with Home Assistant 2024.1.0+
- Python 3.11+ support
- No external dependencies required
- MIT License
- Comprehensive test coverage including edge cases and error scenarios
