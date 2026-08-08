The SOC forecast finally shows the washing machine — and the 24 V support
lane retires.

**Appliance runs on the card.** A running washer or dishwasher has always
shaped the plan: its remaining energy is booked into the AC consumption
forecast the moment it is detected. But the forecast card showed nothing of
it — the SOC curve just dipped for no visible reason. Now every detected run
renders as its own lane below the plot, from now to the run's end, with a
legend entry showing the remaining energy and an "active" marker. The data
rides on the SOC forecast sensor as the new `appliances` attribute
(`appliance_plans` in the coordinator), so ApexCharts and other custom
dashboards can read it too.

**Grid-support lanes removed.** The 24 V / 48 V support lanes below the chart
are gone — operator verdict: "die Info nützt keinem". Only the visualization
retires: the grid support itself (switches, SOC thresholds, planner
behaviour) is completely untouched, and the backend still publishes the
`dc24`/`dc48` forecast flags for any third-party card that reads them.

Update, restart, reload the dashboard — the card module is cache-busted by
the integration version, so no manual resource refresh is needed.

691 tests green (new: appliance plans published for the card + sensor
attribute). Coverage gates unchanged: planner core 100 %, integration
total ≥ 95 %.
