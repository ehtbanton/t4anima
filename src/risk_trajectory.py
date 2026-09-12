#!/usr/bin/env python3
"""
Risk Trajectory Visualizer

Shows how a patient's stroke risk changes over time as new data arrives.
Creates interactive HTML charts with nodes at each data point.
"""

import json
import argparse
from datetime import datetime
from typing import List, Dict, Tuple
from dataclasses import dataclass
import math

@dataclass
class TimelineEvent:
    date: datetime
    event_type: str  # "diagnosis", "blood_result", "encounter", "hospital"
    description: str
    data: dict
    cumulative_risk: float = 0.0

def calculate_risk_at_point(
    age_at_point: int,
    sex: str,
    conditions_so_far: List[str],
    blood_results_so_far: List[dict],
    smoking: bool,
    systolic_bp: float
) -> float:
    """Calculate stroke risk based on data available up to this point."""

    # Base risk by age
    base_risk = 0.001 * math.exp(0.08 * (age_at_point - 40))
    base_risk = min(base_risk, 0.15)

    multiplier = 1.0

    # Condition multipliers
    condition_set = set(c.lower() for c in conditions_so_far)

    if any("atrial fibrillation" in c or "afib" in c for c in condition_set):
        multiplier *= 5.0
    if any("stroke" in c or "tia" in c for c in condition_set):
        multiplier *= 3.0
    if any("hypertension" in c for c in condition_set):
        multiplier *= 2.0
    if any("diabetes" in c for c in condition_set):
        multiplier *= 1.5
    if any("heart failure" in c for c in condition_set):
        multiplier *= 1.5
    if any("ckd" in c or "kidney" in c for c in condition_set):
        multiplier *= 1.3

    if smoking:
        multiplier *= 1.5
    if sex == "M":
        multiplier *= 1.2
    if systolic_bp > 140:
        multiplier *= 1.0 + (systolic_bp - 140) * 0.01

    # Blood result modifiers
    for br in blood_results_so_far:
        for a in br.get("analytes", []):
            name = a.get("name", "").lower()
            value = a.get("value", 0)
            ref_high = a.get("ref_high", 100)

            if "hba1c" in name and value > 53:
                multiplier *= 1.1
            if "egfr" in name and value < 60:
                multiplier *= 1.2
            if "cholesterol" in name and value > ref_high:
                multiplier *= 1.1

    return min(base_risk * multiplier, 0.95)


def build_patient_trajectory(patient: dict) -> List[TimelineEvent]:
    """Build chronological timeline of events with cumulative risk."""

    events = []
    base_date = datetime(2026, 9, 1)

    # Get patient baseline
    age = patient["age"]
    sex = patient["sex"]
    smoking = patient["smoking"]
    systolic_bp = patient["systolic_bp"]

    # Collect all events with dates
    for prob in patient.get("problems", []):
        try:
            date = datetime.strptime(prob["date"], "%Y-%m-%d")
            events.append(TimelineEvent(
                date=date,
                event_type="diagnosis",
                description=prob["term"],
                data=prob
            ))
        except:
            pass

    for br in patient.get("blood_results", []):
        try:
            date = datetime.fromtimestamp(br["collected_at"] / 1000)

            # Summarize abnormals
            abnormals = []
            for a in br.get("analytes", []):
                if a["value"] < a["ref_low"]:
                    abnormals.append(f"{a['name']} LOW")
                elif a["value"] > a["ref_high"]:
                    abnormals.append(f"{a['name']} HIGH")

            desc = br["panel_name"]
            if abnormals:
                desc += f": {', '.join(abnormals)}"
            else:
                desc += ": normal"

            events.append(TimelineEvent(
                date=date,
                event_type="blood_result",
                description=desc,
                data=br
            ))
        except:
            pass

    for enc in patient.get("encounters", []):
        try:
            date = datetime.fromtimestamp(enc["date"] / 1000)
            events.append(TimelineEvent(
                date=date,
                event_type="encounter",
                description=f"GP {enc.get('channel', '')}: {enc.get('reason', '')}",
                data=enc
            ))
        except:
            pass

    for hosp in patient.get("hospital_attendances", []):
        try:
            date = datetime.fromtimestamp(hosp["date"] / 1000)
            events.append(TimelineEvent(
                date=date,
                event_type="hospital",
                description=f"A&E: {hosp.get('presenting_complaint', '')}",
                data=hosp
            ))
        except:
            pass

    # Sort chronologically
    events.sort(key=lambda e: e.date)

    # Calculate cumulative risk at each point
    conditions_so_far = []
    blood_results_so_far = []

    for event in events:
        # Update cumulative data
        if event.event_type == "diagnosis":
            term = event.data.get("term", "")
            if term and term not in ["Medication review", "Preventive health review",
                                      "Annual review", "Follow-up", "Routine blood test"]:
                conditions_so_far.append(term)

        if event.event_type == "blood_result":
            blood_results_so_far.append(event.data)

        # Calculate age at this point
        years_before_now = (base_date - event.date).days / 365
        age_at_point = max(18, age - int(years_before_now))

        # Calculate risk
        event.cumulative_risk = calculate_risk_at_point(
            age_at_point=age_at_point,
            sex=sex,
            conditions_so_far=conditions_so_far,
            blood_results_so_far=blood_results_so_far,
            smoking=smoking,
            systolic_bp=systolic_bp
        )

    return events


def generate_html_chart(patient: dict, events: List[TimelineEvent]) -> str:
    """Generate interactive HTML chart using Chart.js."""

    # Prepare data for chart
    dates = [e.date.strftime("%Y-%m-%d") for e in events]
    risks = [round(e.cumulative_risk * 100, 1) for e in events]
    labels = [e.description[:50] for e in events]
    colors = []

    for e in events:
        if e.event_type == "diagnosis":
            colors.append("rgba(255, 99, 132, 0.8)")  # Red
        elif e.event_type == "blood_result":
            colors.append("rgba(54, 162, 235, 0.8)")  # Blue
        elif e.event_type == "encounter":
            colors.append("rgba(75, 192, 192, 0.8)")  # Green
        else:
            colors.append("rgba(255, 159, 64, 0.8)")  # Orange

    # Get conditions list
    conditions = [p["term"] for p in patient.get("problems", [])
                  if p["term"] not in ["Medication review", "Preventive health review",
                                       "Annual review", "Follow-up", "Routine blood test"]]

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Stroke Risk Trajectory - {patient['id']}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns"></script>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }}
        .header {{
            background: linear-gradient(135deg, #1a237e 0%, #0d47a1 100%);
            color: white;
            padding: 30px;
            border-radius: 12px;
            margin-bottom: 20px;
        }}
        .header h1 {{
            margin: 0 0 10px 0;
        }}
        .patient-info {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-top: 15px;
        }}
        .info-box {{
            background: rgba(255,255,255,0.15);
            padding: 10px 15px;
            border-radius: 8px;
        }}
        .info-box .label {{
            font-size: 12px;
            opacity: 0.8;
        }}
        .info-box .value {{
            font-size: 24px;
            font-weight: bold;
        }}
        .chart-container {{
            background: white;
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }}
        .conditions {{
            background: white;
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        .condition-tag {{
            display: inline-block;
            padding: 5px 12px;
            margin: 3px;
            border-radius: 20px;
            font-size: 14px;
        }}
        .condition-tag.risk {{
            background: #ffcdd2;
            color: #c62828;
        }}
        .condition-tag.normal {{
            background: #e3f2fd;
            color: #1565c0;
        }}
        .legend {{
            display: flex;
            gap: 20px;
            margin-top: 15px;
            flex-wrap: wrap;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 14px;
        }}
        .legend-dot {{
            width: 12px;
            height: 12px;
            border-radius: 50%;
        }}
        .risk-zone {{
            margin-top: 20px;
            padding: 15px;
            border-radius: 8px;
            text-align: center;
        }}
        .risk-zone.high {{
            background: #ffebee;
            border: 2px solid #ef5350;
        }}
        .risk-zone.medium {{
            background: #fff3e0;
            border: 2px solid #ff9800;
        }}
        .risk-zone.low {{
            background: #e8f5e9;
            border: 2px solid #66bb6a;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Stroke Risk Trajectory</h1>
        <div style="font-size: 18px; opacity: 0.9;">Patient {patient['id']}</div>
        <div class="patient-info">
            <div class="info-box">
                <div class="label">Age</div>
                <div class="value">{patient['age']}</div>
            </div>
            <div class="info-box">
                <div class="label">Sex</div>
                <div class="value">{patient['sex']}</div>
            </div>
            <div class="info-box">
                <div class="label">Blood Pressure</div>
                <div class="value">{int(patient['systolic_bp'])}/{int(patient['diastolic_bp'])}</div>
            </div>
            <div class="info-box">
                <div class="label">Current Risk</div>
                <div class="value">{risks[-1] if risks else 0}%</div>
            </div>
        </div>
    </div>

    <div class="chart-container">
        <h2>Risk Over Time</h2>
        <canvas id="riskChart"></canvas>
        <div class="legend">
            <div class="legend-item">
                <div class="legend-dot" style="background: rgba(255, 99, 132, 0.8)"></div>
                <span>Diagnosis</span>
            </div>
            <div class="legend-item">
                <div class="legend-dot" style="background: rgba(54, 162, 235, 0.8)"></div>
                <span>Blood Result</span>
            </div>
            <div class="legend-item">
                <div class="legend-dot" style="background: rgba(75, 192, 192, 0.8)"></div>
                <span>GP Encounter</span>
            </div>
            <div class="legend-item">
                <div class="legend-dot" style="background: rgba(255, 159, 64, 0.8)"></div>
                <span>Hospital</span>
            </div>
        </div>

        <div class="risk-zone {'high' if risks and risks[-1] > 20 else 'medium' if risks and risks[-1] > 10 else 'low'}">
            <strong>{'HIGH RISK - Intervention Required' if risks and risks[-1] > 20 else 'MODERATE RISK - Monitor Closely' if risks and risks[-1] > 10 else 'LOW RISK'}</strong>
        </div>
    </div>

    <div class="conditions">
        <h2>Conditions</h2>
        {''.join(f'<span class="condition-tag {"risk" if c in ["Atrial fibrillation", "Hypertension", "Diabetes", "Heart failure", "CKD", "Previous stroke", "Previous TIA"] else "normal"}">{c}</span>' for c in conditions)}
    </div>

    <script>
        const ctx = document.getElementById('riskChart').getContext('2d');

        const data = {{
            labels: {json.dumps(dates)},
            datasets: [{{
                label: '5-Year Stroke Risk (%)',
                data: {json.dumps(risks)},
                borderColor: 'rgb(75, 192, 192)',
                backgroundColor: 'rgba(75, 192, 192, 0.1)',
                fill: true,
                tension: 0.3,
                pointBackgroundColor: {json.dumps(colors)},
                pointBorderColor: {json.dumps(colors)},
                pointRadius: 8,
                pointHoverRadius: 12,
            }}]
        }};

        const tooltipLabels = {json.dumps(labels)};

        new Chart(ctx, {{
            type: 'line',
            data: data,
            options: {{
                responsive: true,
                plugins: {{
                    tooltip: {{
                        callbacks: {{
                            afterLabel: function(context) {{
                                return tooltipLabels[context.dataIndex];
                            }}
                        }}
                    }}
                }},
                scales: {{
                    y: {{
                        beginAtZero: true,
                        max: Math.max(50, Math.max(...{json.dumps(risks)}) + 10),
                        title: {{
                            display: true,
                            text: '5-Year Stroke Risk (%)'
                        }}
                    }},
                    x: {{
                        title: {{
                            display: true,
                            text: 'Date'
                        }}
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>"""

    return html


def main():
    parser = argparse.ArgumentParser(description="Generate risk trajectory visualizations")
    parser.add_argument("--data", default="data/patients.json", help="Patient data file")
    parser.add_argument("--patient", help="Specific patient ID to visualize")
    parser.add_argument("--output-dir", default="visualizations", help="Output directory")
    parser.add_argument("--top-n", type=int, default=10, help="Generate charts for top N highest risk patients")

    args = parser.parse_args()

    import os
    os.makedirs(args.output_dir, exist_ok=True)

    with open(args.data) as f:
        patients = json.load(f)

    if args.patient:
        # Single patient
        patient = next((p for p in patients if p["id"] == args.patient), None)
        if not patient:
            print(f"Patient {args.patient} not found")
            return

        events = build_patient_trajectory(patient)
        html = generate_html_chart(patient, events)

        output_path = os.path.join(args.output_dir, f"{patient['id']}_trajectory.html")
        with open(output_path, "w") as f:
            f.write(html)
        print(f"Generated: {output_path}")

    else:
        # Top N highest risk
        sorted_patients = sorted(patients, key=lambda p: p["stroke_risk_5yr"], reverse=True)

        for patient in sorted_patients[:args.top_n]:
            events = build_patient_trajectory(patient)
            html = generate_html_chart(patient, events)

            output_path = os.path.join(args.output_dir, f"{patient['id']}_trajectory.html")
            with open(output_path, "w") as f:
                f.write(html)
            print(f"Generated: {output_path} (risk: {patient['stroke_risk_5yr']*100:.1f}%)")

        # Generate index
        index_html = """<!DOCTYPE html>
<html>
<head>
    <title>Stroke Risk Trajectories</title>
    <style>
        body { font-family: sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
        .patient-link { display: block; padding: 15px; margin: 10px 0; background: #f5f5f5;
                       border-radius: 8px; text-decoration: none; color: #333; }
        .patient-link:hover { background: #e0e0e0; }
        .risk { float: right; font-weight: bold; }
        .risk.high { color: #c62828; }
        .risk.medium { color: #f57c00; }
    </style>
</head>
<body>
    <h1>Stroke Risk Trajectories</h1>
    <p>Top """ + str(args.top_n) + """ highest risk patients:</p>
"""
        for patient in sorted_patients[:args.top_n]:
            risk = patient['stroke_risk_5yr'] * 100
            risk_class = "high" if risk > 20 else "medium"
            index_html += f"""    <a class="patient-link" href="{patient['id']}_trajectory.html">
        {patient['id']} - Age {patient['age']}, {patient['sex']}
        <span class="risk {risk_class}">{risk:.1f}% risk</span>
    </a>\n"""

        index_html += "</body></html>"

        with open(os.path.join(args.output_dir, "index.html"), "w") as f:
            f.write(index_html)
        print(f"\nGenerated index: {args.output_dir}/index.html")


if __name__ == "__main__":
    main()
