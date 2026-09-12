#!/usr/bin/env python3
"""
Stroke Risk Classifier

Trains a classifier to predict 5-year stroke risk from patient timelines.

Two approaches:
1. Embedding-based: Sentence transformer + Logistic Regression
2. LLM-based: Direct inference using GPT-4o-mini (for demo fallback)

Usage:
    python stroke_classifier.py train --data data/patients.json --output models/stroke_model.pkl
    python stroke_classifier.py predict --model models/stroke_model.pkl --patient "timeline text..."
    python stroke_classifier.py evaluate --data data/patients.json --model models/stroke_model.pkl
"""

import json
import pickle
import argparse
import os
from typing import List, Tuple, Dict
import numpy as np
from dataclasses import dataclass

# =============================================================================
# EMBEDDINGS-BASED CLASSIFIER
# =============================================================================

class StrokeRiskClassifier:
    """Embedding-based stroke risk classifier."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self.embedder = None
        self.classifier = None
        self.threshold = 0.5

    def _load_embedder(self):
        if self.embedder is None:
            from sentence_transformers import SentenceTransformer
            print(f"Loading embedding model: {self.model_name}")
            self.embedder = SentenceTransformer(self.model_name)

    def _patient_to_text(self, patient: dict) -> str:
        """Convert patient JSON to text timeline."""
        lines = []

        # Demographics
        lines.append(f"Age {patient['age']} {patient['sex']} Smoker:{patient['smoking']} BP:{patient['systolic_bp']}/{patient['diastolic_bp']} BMI:{patient['bmi']}")

        # Problems
        for p in patient.get("problems", []):
            lines.append(f"{p['date']}: {p['term']} ({p['status']})")

        # Blood results - focus on abnormals
        for br in patient.get("blood_results", []):
            abnormals = []
            for a in br.get("analytes", []):
                if a["value"] < a["ref_low"]:
                    abnormals.append(f"{a['name']} LOW")
                elif a["value"] > a["ref_high"]:
                    abnormals.append(f"{a['name']} HIGH")
            if abnormals:
                lines.append(f"Bloods: {', '.join(abnormals)}")

        # Hospital attendances
        for h in patient.get("hospital_attendances", []):
            lines.append(f"A&E: {h['presenting_complaint']} -> {h['outcome']}")

        return "\n".join(lines)

    def train(self, patients: List[dict], test_size: float = 0.2):
        """Train classifier on patient data."""
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import classification_report, roc_auc_score

        self._load_embedder()

        # Prepare data
        print("Converting patients to text...")
        texts = [self._patient_to_text(p) for p in patients]
        labels = np.array([1 if p["will_have_stroke"] else 0 for p in patients])

        print(f"Embedding {len(texts)} patients...")
        embeddings = self.embedder.encode(texts, show_progress_bar=True)

        # Split
        X_train, X_test, y_train, y_test = train_test_split(
            embeddings, labels, test_size=test_size, random_state=42, stratify=labels
        )

        print(f"Training set: {len(X_train)} ({sum(y_train)} strokes)")
        print(f"Test set: {len(X_test)} ({sum(y_test)} strokes)")

        # Train
        print("Training classifier...")
        self.classifier = LogisticRegression(
            class_weight="balanced",  # Handle class imbalance
            max_iter=1000,
            random_state=42
        )
        self.classifier.fit(X_train, y_train)

        # Evaluate
        y_pred = self.classifier.predict(X_test)
        y_prob = self.classifier.predict_proba(X_test)[:, 1]

        print("\n" + "="*60)
        print("CLASSIFICATION REPORT")
        print("="*60)
        print(classification_report(y_test, y_pred, target_names=["No Stroke", "Stroke"]))

        try:
            auc = roc_auc_score(y_test, y_prob)
            print(f"ROC AUC: {auc:.3f}")
        except:
            print("Could not calculate AUC")

        return {
            "train_size": len(X_train),
            "test_size": len(X_test),
            "auc": auc if 'auc' in dir() else None
        }

    def predict(self, patient: dict) -> Tuple[bool, float]:
        """Predict stroke risk for a single patient."""
        self._load_embedder()

        text = self._patient_to_text(patient)
        embedding = self.embedder.encode([text])

        prob = self.classifier.predict_proba(embedding)[0, 1]
        prediction = prob > self.threshold

        return prediction, prob

    def predict_text(self, timeline_text: str) -> Tuple[bool, float]:
        """Predict from raw timeline text."""
        self._load_embedder()

        embedding = self.embedder.encode([timeline_text])
        prob = self.classifier.predict_proba(embedding)[0, 1]
        prediction = prob > self.threshold

        return prediction, prob

    def save(self, path: str):
        """Save model to file."""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({
                "model_name": self.model_name,
                "classifier": self.classifier,
                "threshold": self.threshold,
            }, f)
        print(f"Model saved to {path}")

    @classmethod
    def load(cls, path: str) -> "StrokeRiskClassifier":
        """Load model from file."""
        with open(path, "rb") as f:
            data = pickle.load(f)

        model = cls(model_name=data["model_name"])
        model.classifier = data["classifier"]
        model.threshold = data["threshold"]
        return model


# =============================================================================
# LLM-BASED CLASSIFIER (Fallback/Demo)
# =============================================================================

class LLMStrokeClassifier:
    """LLM-based stroke risk classifier using GPT-4o-mini."""

    SYSTEM_PROMPT = """You are a clinical risk assessment AI. Given a patient's medical timeline, estimate their 5-year stroke risk.

Consider key risk factors:
- Atrial fibrillation (5x risk)
- Previous stroke/TIA (3x risk)
- Hypertension (2x risk)
- Diabetes (1.5x risk)
- Heart failure (1.5x risk)
- Age >65 (increased risk)
- Smoking (1.5x risk)
- Abnormal blood results (eGFR, cholesterol)

Respond with ONLY a JSON object:
{"risk_level": "low|medium|high|very_high", "risk_percent": <0-100>, "key_factors": ["factor1", "factor2"], "recommendation": "brief clinical recommendation"}"""

    def __init__(self, api_key: str = None):
        import openai
        self.client = openai.OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))

    def predict(self, timeline_text: str) -> dict:
        """Predict stroke risk using LLM."""
        response = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": f"Patient timeline:\n\n{timeline_text}"}
            ],
            temperature=0,
            response_format={"type": "json_object"}
        )

        import json
        result = json.loads(response.choices[0].message.content)
        return result


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Stroke Risk Classifier")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Train command
    train_parser = subparsers.add_parser("train", help="Train the classifier")
    train_parser.add_argument("--data", required=True, help="Path to patients.json")
    train_parser.add_argument("--output", default="models/stroke_model.pkl", help="Output model path")
    train_parser.add_argument("--test-size", type=float, default=0.2, help="Test set fraction")

    # Evaluate command
    eval_parser = subparsers.add_parser("evaluate", help="Evaluate model on data")
    eval_parser.add_argument("--data", required=True, help="Path to patients.json")
    eval_parser.add_argument("--model", required=True, help="Path to trained model")

    # Predict command
    predict_parser = subparsers.add_parser("predict", help="Predict for a patient")
    predict_parser.add_argument("--model", required=True, help="Path to trained model")
    predict_parser.add_argument("--patient", help="Patient timeline text")
    predict_parser.add_argument("--patient-file", help="Path to patient JSON file")

    # LLM predict command
    llm_parser = subparsers.add_parser("llm-predict", help="Predict using LLM")
    llm_parser.add_argument("--timeline", required=True, help="Patient timeline text")

    args = parser.parse_args()

    if args.command == "train":
        print(f"Loading data from {args.data}...")
        with open(args.data) as f:
            patients = json.load(f)

        classifier = StrokeRiskClassifier()
        classifier.train(patients, test_size=args.test_size)
        classifier.save(args.output)

    elif args.command == "evaluate":
        print(f"Loading model from {args.model}...")
        classifier = StrokeRiskClassifier.load(args.model)

        print(f"Loading data from {args.data}...")
        with open(args.data) as f:
            patients = json.load(f)

        # Predict on all
        correct = 0
        high_risk_caught = 0
        high_risk_total = 0

        for p in patients:
            pred, prob = classifier.predict(p)
            actual = p["will_have_stroke"]

            if pred == actual:
                correct += 1

            if p["stroke_risk_5yr"] > 0.1:  # High risk
                high_risk_total += 1
                if pred:
                    high_risk_caught += 1

        print(f"Accuracy: {correct}/{len(patients)} ({100*correct/len(patients):.1f}%)")
        print(f"High-risk patients caught: {high_risk_caught}/{high_risk_total} ({100*high_risk_caught/high_risk_total:.1f}%)")

    elif args.command == "predict":
        classifier = StrokeRiskClassifier.load(args.model)

        if args.patient_file:
            with open(args.patient_file) as f:
                patient = json.load(f)
            pred, prob = classifier.predict(patient)
        else:
            pred, prob = classifier.predict_text(args.patient)

        print(f"Stroke Risk: {prob*100:.1f}%")
        print(f"Prediction: {'HIGH RISK' if pred else 'Low risk'}")

    elif args.command == "llm-predict":
        classifier = LLMStrokeClassifier()
        result = classifier.predict(args.timeline)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
