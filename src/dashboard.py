#!/usr/bin/env python3
"""
Interactive Stroke Risk Dashboard

Scrollable patient list with risk trajectory charts.
"""

import json
import argparse
from datetime import datetime
import os

def build_trajectory_data(patient: dict) -> dict:
    """Build trajectory data points for a patient."""
    events = []
    base_date = datetime(2026, 9, 12)

    # Collect all timestamped events
    for prob in patient.get("problems", []):
        try:
            date = datetime.strptime(prob["date"], "%Y-%m-%d")
            term = prob["term"]
            # Skip routine items for the chart
            if term not in ["Medication review", "Routine review", "Annual review"]:
                events.append({
                    "date": date,
                    "type": "diagnosis",
                    "label": term,
                    "color": "rgba(220, 53, 69, 0.9)"  # Red
                })
        except:
            pass

    for br in patient.get("blood_results", []):
        try:
            date = datetime.fromtimestamp(br["collected_at"] / 1000)
            abnormals = []
            for a in br.get("analytes", []):
                if a["value"] < a["ref_low"]:
                    abnormals.append(f"{a['name']}↓")
                elif a["value"] > a["ref_high"]:
                    abnormals.append(f"{a['name']}↑")

            label = br["panel_name"]
            if abnormals:
                label += f": {', '.join(abnormals[:2])}"

            events.append({
                "date": date,
                "type": "blood",
                "label": label,
                "color": "rgba(0, 123, 255, 0.9)" if not abnormals else "rgba(255, 193, 7, 0.9)"
            })
        except:
            pass

    for enc in patient.get("encounters", []):
        try:
            date = datetime.fromtimestamp(enc["date"] / 1000)
            events.append({
                "date": date,
                "type": "encounter",
                "label": f"GP: {enc.get('reason', 'Visit')[:30]}",
                "color": "rgba(40, 167, 69, 0.7)"  # Green
            })
        except:
            pass

    for hosp in patient.get("hospital_attendances", []):
        try:
            date = datetime.fromtimestamp(hosp["date"] / 1000)
            events.append({
                "date": date,
                "type": "hospital",
                "label": f"A&E: {hosp.get('presenting_complaint', '')}",
                "color": "rgba(111, 66, 193, 0.9)"  # Purple
            })
        except:
            pass

    # Sort and calculate cumulative risk
    events.sort(key=lambda e: e["date"])

    # Calculate risk at each point
    conditions_so_far = set()
    risk_multiplier = 1.0
    age = patient["age"]
    base_risk = 0.001 * (2.718 ** (0.08 * (age - 40)))
    base_risk = min(base_risk, 0.15)

    risk_points = []
    for e in events:
        if e["type"] == "diagnosis":
            term = e["label"].lower()
            if "atrial fibrillation" in term: risk_multiplier *= 1.8
            elif "stroke" in term or "tia" in term: risk_multiplier *= 1.5
            elif "hypertension" in term: risk_multiplier *= 1.4
            elif "diabetes" in term: risk_multiplier *= 1.3
            elif "heart failure" in term: risk_multiplier *= 1.3
            elif "kidney" in term: risk_multiplier *= 1.2

        risk = min(base_risk * risk_multiplier, 0.95) * 100

        risk_points.append({
            "x": e["date"].strftime("%Y-%m-%d"),
            "y": round(risk, 1),
            "label": e["label"],
            "type": e["type"],
            "color": e["color"]
        })

    return risk_points


def generate_dashboard(patients: list, output_path: str, max_patients: int = 100):
    """Generate interactive HTML dashboard."""

    # Sort by risk
    sorted_patients = sorted(patients, key=lambda p: p["stroke_risk_5yr"], reverse=True)[:max_patients]

    # Build patient data
    patients_json = []
    for p in sorted_patients:
        trajectory = build_trajectory_data(p)
        conditions = [prob["term"] for prob in p.get("problems", [])
                     if prob["term"] not in ["Medication review", "Routine review", "Annual review"]]

        patients_json.append({
            "id": p["id"],
            "age": p["age"],
            "sex": p["sex"],
            "bp": f"{int(p['systolic_bp'])}/{int(p['diastolic_bp'])}",
            "bmi": p["bmi"],
            "smoking": p["smoking"],
            "risk": round(p["stroke_risk_5yr"] * 100, 1),
            "willStroke": p["will_have_stroke"],
            "conditions": conditions,
            "trajectory": trajectory,
            "encounters": len(p.get("encounters", [])),
            "bloods": len(p.get("blood_results", [])),
        })

    html = """<!DOCTYPE html>
<html>
<head>
    <title>Stroke Risk Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f172a;
            color: #e2e8f0;
            display: flex;
            height: 100vh;
            overflow: hidden;
        }

        /* Sidebar - Patient List */
        .sidebar {
            width: 380px;
            background: #1e293b;
            border-right: 1px solid #334155;
            display: flex;
            flex-direction: column;
            flex-shrink: 0;
        }
        .sidebar-header {
            padding: 20px;
            background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%);
        }
        .sidebar-header h1 {
            font-size: 1.5rem;
            margin-bottom: 5px;
        }
        .sidebar-header p {
            opacity: 0.8;
            font-size: 0.875rem;
        }
        .search-box {
            padding: 15px 20px;
            border-bottom: 1px solid #334155;
        }
        .search-box input {
            width: 100%;
            padding: 10px 15px;
            border: 1px solid #475569;
            border-radius: 8px;
            background: #0f172a;
            color: #e2e8f0;
            font-size: 0.875rem;
        }
        .patient-list {
            flex: 1;
            overflow-y: auto;
        }
        .patient-item {
            padding: 15px 20px;
            border-bottom: 1px solid #334155;
            cursor: pointer;
            transition: background 0.2s;
        }
        .patient-item:hover {
            background: #334155;
        }
        .patient-item.active {
            background: #1d4ed8;
        }
        .patient-item .top-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 5px;
        }
        .patient-item .id {
            font-weight: 600;
            font-size: 0.9rem;
        }
        .patient-item .risk {
            font-weight: 700;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 0.75rem;
        }
        .patient-item .risk.high { background: #dc2626; }
        .patient-item .risk.medium { background: #f59e0b; color: #000; }
        .patient-item .risk.low { background: #22c55e; color: #000; }
        .patient-item .details {
            font-size: 0.8rem;
            color: #94a3b8;
        }
        .patient-item .conditions {
            font-size: 0.75rem;
            color: #cbd5e1;
            margin-top: 5px;
        }

        /* Main Content */
        .main {
            flex: 1;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }
        .patient-header {
            padding: 25px 30px;
            background: #1e293b;
            border-bottom: 1px solid #334155;
        }
        .patient-header h2 {
            font-size: 1.75rem;
            margin-bottom: 15px;
        }
        .patient-header .stroke-label {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 600;
            margin-left: 10px;
        }
        .patient-header .stroke-label.yes {
            background: #dc2626;
        }
        .patient-header .stroke-label.no {
            background: #22c55e;
            color: #000;
        }
        .stats-row {
            display: flex;
            gap: 30px;
            flex-wrap: wrap;
        }
        .stat {
            background: #334155;
            padding: 12px 20px;
            border-radius: 8px;
        }
        .stat .label {
            font-size: 0.7rem;
            text-transform: uppercase;
            color: #94a3b8;
            margin-bottom: 3px;
        }
        .stat .value {
            font-size: 1.25rem;
            font-weight: 600;
        }

        .chart-container {
            flex: 1;
            padding: 30px;
            overflow-y: auto;
        }
        .chart-wrapper {
            background: #1e293b;
            border-radius: 12px;
            padding: 25px;
            margin-bottom: 20px;
        }
        .chart-wrapper h3 {
            margin-bottom: 20px;
            font-size: 1.1rem;
        }
        #trajectoryChart {
            max-height: 400px;
        }

        .conditions-section {
            background: #1e293b;
            border-radius: 12px;
            padding: 25px;
        }
        .conditions-section h3 {
            margin-bottom: 15px;
        }
        .condition-tag {
            display: inline-block;
            padding: 6px 14px;
            margin: 4px;
            border-radius: 20px;
            font-size: 0.8rem;
        }
        .condition-tag.risk {
            background: #7f1d1d;
            border: 1px solid #dc2626;
        }
        .condition-tag.normal {
            background: #1e3a5f;
            border: 1px solid #3b82f6;
        }

        .legend {
            display: flex;
            gap: 20px;
            margin-top: 15px;
            flex-wrap: wrap;
        }
        .legend-item {
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 0.8rem;
            color: #94a3b8;
        }
        .legend-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
        }
    </style>
</head>
<body>
    <div class="sidebar">
        <div class="sidebar-header">
            <h1>Stroke Risk Dashboard</h1>
            <p>""" + str(len(patients_json)) + """ patients sorted by risk</p>
        </div>
        <div class="search-box">
            <input type="text" id="searchInput" placeholder="Search patients..." oninput="filterPatients()">
        </div>
        <div class="patient-list" id="patientList"></div>
    </div>

    <div class="main">
        <div class="patient-header" id="patientHeader">
            <h2>Select a patient</h2>
        </div>
        <div class="chart-container" id="chartContainer">
            <div class="chart-wrapper">
                <h3>Risk Trajectory Over Time</h3>
                <canvas id="trajectoryChart"></canvas>
                <div class="legend">
                    <div class="legend-item"><div class="legend-dot" style="background: rgba(220, 53, 69, 0.9)"></div>Diagnosis</div>
                    <div class="legend-item"><div class="legend-dot" style="background: rgba(0, 123, 255, 0.9)"></div>Blood Test</div>
                    <div class="legend-item"><div class="legend-dot" style="background: rgba(255, 193, 7, 0.9)"></div>Abnormal Result</div>
                    <div class="legend-item"><div class="legend-dot" style="background: rgba(40, 167, 69, 0.7)"></div>GP Encounter</div>
                    <div class="legend-item"><div class="legend-dot" style="background: rgba(111, 66, 193, 0.9)"></div>Hospital</div>
                </div>
            </div>
            <div class="conditions-section" id="conditionsSection"></div>
        </div>
    </div>

    <script>
        const patients = """ + json.dumps(patients_json) + """;
        let chart = null;
        let selectedIndex = 0;

        function renderPatientList(filteredPatients = patients) {
            const list = document.getElementById('patientList');
            list.innerHTML = filteredPatients.map((p, i) => {
                const riskClass = p.risk > 20 ? 'high' : p.risk > 10 ? 'medium' : 'low';
                const conditionsStr = p.conditions.slice(0, 3).join(', ');
                return `
                    <div class="patient-item ${i === selectedIndex ? 'active' : ''}"
                         onclick="selectPatient(${patients.indexOf(p)})">
                        <div class="top-row">
                            <span class="id">${p.id}</span>
                            <span class="risk ${riskClass}">${p.risk}%</span>
                        </div>
                        <div class="details">Age ${p.age} • ${p.sex} • BP ${p.bp}</div>
                        <div class="conditions">${conditionsStr || 'No major conditions'}</div>
                    </div>
                `;
            }).join('');
        }

        function filterPatients() {
            const query = document.getElementById('searchInput').value.toLowerCase();
            const filtered = patients.filter(p =>
                p.id.toLowerCase().includes(query) ||
                p.conditions.some(c => c.toLowerCase().includes(query))
            );
            renderPatientList(filtered);
        }

        function selectPatient(index) {
            selectedIndex = index;
            const p = patients[index];

            // Update header
            document.getElementById('patientHeader').innerHTML = `
                <h2>${p.id}
                    <span class="stroke-label ${p.willStroke ? 'yes' : 'no'}">
                        ${p.willStroke ? 'WILL HAVE STROKE' : 'NO STROKE'}
                    </span>
                </h2>
                <div class="stats-row">
                    <div class="stat"><div class="label">Age</div><div class="value">${p.age}</div></div>
                    <div class="stat"><div class="label">Sex</div><div class="value">${p.sex}</div></div>
                    <div class="stat"><div class="label">Blood Pressure</div><div class="value">${p.bp}</div></div>
                    <div class="stat"><div class="label">BMI</div><div class="value">${p.bmi}</div></div>
                    <div class="stat"><div class="label">Smoker</div><div class="value">${p.smoking ? 'Yes' : 'No'}</div></div>
                    <div class="stat"><div class="label">5yr Risk</div><div class="value" style="color: ${p.risk > 20 ? '#ef4444' : p.risk > 10 ? '#f59e0b' : '#22c55e'}">${p.risk}%</div></div>
                    <div class="stat"><div class="label">Encounters</div><div class="value">${p.encounters}</div></div>
                    <div class="stat"><div class="label">Blood Tests</div><div class="value">${p.bloods}</div></div>
                </div>
            `;

            // Update conditions
            const riskConditions = ['Atrial fibrillation', 'Hypertension', 'Type 2 Diabetes',
                                   'Heart failure', 'Chronic kidney disease', 'Previous stroke', 'Previous TIA'];
            document.getElementById('conditionsSection').innerHTML = `
                <h3>Conditions</h3>
                ${p.conditions.map(c =>
                    `<span class="condition-tag ${riskConditions.includes(c) ? 'risk' : 'normal'}">${c}</span>`
                ).join('')}
            `;

            // Update chart
            updateChart(p);

            // Update list selection
            renderPatientList();
        }

        function updateChart(p) {
            const ctx = document.getElementById('trajectoryChart').getContext('2d');

            if (chart) {
                chart.destroy();
            }

            const trajectory = p.trajectory;

            chart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: trajectory.map(t => t.x),
                    datasets: [{
                        label: 'Stroke Risk %',
                        data: trajectory.map(t => t.y),
                        borderColor: 'rgba(59, 130, 246, 1)',
                        backgroundColor: 'rgba(59, 130, 246, 0.1)',
                        fill: true,
                        tension: 0.3,
                        pointBackgroundColor: trajectory.map(t => t.color),
                        pointBorderColor: trajectory.map(t => t.color),
                        pointRadius: 6,
                        pointHoverRadius: 10,
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        tooltip: {
                            callbacks: {
                                afterLabel: function(context) {
                                    return trajectory[context.dataIndex].label;
                                }
                            }
                        },
                        legend: { display: false }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            max: Math.max(50, Math.max(...trajectory.map(t => t.y)) + 10),
                            grid: { color: 'rgba(148, 163, 184, 0.1)' },
                            ticks: { color: '#94a3b8' },
                            title: { display: true, text: 'Stroke Risk %', color: '#94a3b8' }
                        },
                        x: {
                            grid: { color: 'rgba(148, 163, 184, 0.1)' },
                            ticks: { color: '#94a3b8', maxRotation: 45 }
                        }
                    }
                }
            });
        }

        // Initialize
        renderPatientList();
        selectPatient(0);
    </script>
</body>
</html>"""

    with open(output_path, "w") as f:
        f.write(html)
    print(f"Dashboard saved to {output_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/patients_v2.json")
    parser.add_argument("--output", default="dashboard.html")
    parser.add_argument("--max-patients", type=int, default=100)
    args = parser.parse_args()

    with open(args.data) as f:
        patients = json.load(f)

    generate_dashboard(patients, args.output, args.max_patients)


if __name__ == "__main__":
    main()
