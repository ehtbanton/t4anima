#!/usr/bin/env python3
"""
Train stroke risk classifier on patient report text.
Uses sentence transformers to embed concatenated clinical narratives.
"""

import json
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import roc_auc_score, classification_report, precision_recall_curve, average_precision_score
from sentence_transformers import SentenceTransformer
import pickle
import argparse

def build_patient_text(patient, up_to_event_idx=None):
    """Concatenate all report text for a patient up to a given event index."""
    events = patient['events']
    if up_to_event_idx is not None:
        events = events[:up_to_event_idx]

    # Build comprehensive text representation
    parts = []

    # Demographics
    parts.append(f"Patient demographics: {patient['age']} year old {patient['sex']}.")
    parts.append(f"Blood pressure: {patient['sbp']}/{patient['dbp']} mmHg.")
    if patient['smoking']:
        parts.append("Current smoker.")

    # Conditions diagnosed up to this point
    diagnosed = [e['detail'] for e in events if e['type'] == 'diagnosis']
    if diagnosed:
        parts.append(f"Diagnosed conditions: {', '.join(diagnosed)}.")

    # All report texts
    for e in events:
        if e.get('report_text'):
            parts.append(f"--- {e['date']} {e['type'].upper()} ---")
            parts.append(e['report_text'])

    return "\n\n".join(parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/patients_50k.json")
    parser.add_argument("--train-size", type=int, default=40000)
    parser.add_argument("--model-out", default="models/text_classifier.pkl")
    parser.add_argument("--embeddings-out", default="models/embeddings_50k.npy")
    args = parser.parse_args()

    print("Loading patient data...")
    with open(args.input) as f:
        patients = json.load(f)

    print(f"Total patients: {len(patients)}")
    print(f"Stroke rate: {100 * sum(p['will_stroke'] for p in patients) / len(patients):.1f}%")

    # Split into train/test
    train_patients = patients[:args.train_size]
    test_patients = patients[args.train_size:]

    print(f"\nTrain set: {len(train_patients)}")
    print(f"Test set: {len(test_patients)}")
    print(f"Train strokes: {sum(p['will_stroke'] for p in train_patients)} ({100*sum(p['will_stroke'] for p in train_patients)/len(train_patients):.1f}%)")
    print(f"Test strokes: {sum(p['will_stroke'] for p in test_patients)} ({100*sum(p['will_stroke'] for p in test_patients)/len(test_patients):.1f}%)")

    # Build text for each patient
    print("\nBuilding patient text representations...")
    train_texts = [build_patient_text(p) for p in train_patients]
    test_texts = [build_patient_text(p) for p in test_patients]

    # Show sample
    print(f"\nSample patient text length: {len(train_texts[0])} chars")
    print(f"Average text length: {np.mean([len(t) for t in train_texts]):.0f} chars")

    # Embed with sentence transformer
    print("\nLoading sentence transformer (all-MiniLM-L6-v2)...")
    model = SentenceTransformer('all-MiniLM-L6-v2')

    print("Embedding train texts...")
    # Process in batches for memory efficiency
    batch_size = 500
    train_embeddings = []
    for i in range(0, len(train_texts), batch_size):
        batch = train_texts[i:i+batch_size]
        emb = model.encode(batch, show_progress_bar=False)
        train_embeddings.append(emb)
        print(f"  {min(i+batch_size, len(train_texts))}/{len(train_texts)}")
    train_embeddings = np.vstack(train_embeddings)

    print("Embedding test texts...")
    test_embeddings = []
    for i in range(0, len(test_texts), batch_size):
        batch = test_texts[i:i+batch_size]
        emb = model.encode(batch, show_progress_bar=False)
        test_embeddings.append(emb)
        print(f"  {min(i+batch_size, len(test_texts))}/{len(test_texts)}")
    test_embeddings = np.vstack(test_embeddings)

    print(f"\nEmbedding shape: {train_embeddings.shape}")

    # Train labels
    y_train = np.array([p['will_stroke'] for p in train_patients])
    y_test = np.array([p['will_stroke'] for p in test_patients])

    # Train classifier
    print("\nTraining Gradient Boosting classifier...")
    clf = GradientBoostingClassifier(
        n_estimators=200,
        max_depth=5,
        learning_rate=0.1,
        random_state=42,
        verbose=1
    )
    clf.fit(train_embeddings, y_train)

    # Evaluate
    print("\n" + "="*60)
    print("EVALUATION ON TEST SET")
    print("="*60)

    train_proba = clf.predict_proba(train_embeddings)[:, 1]
    test_proba = clf.predict_proba(test_embeddings)[:, 1]

    train_auc = roc_auc_score(y_train, train_proba)
    test_auc = roc_auc_score(y_test, test_proba)

    print(f"\nTrain ROC AUC: {train_auc:.4f}")
    print(f"Test ROC AUC:  {test_auc:.4f}")

    # Average precision (better for imbalanced)
    train_ap = average_precision_score(y_train, train_proba)
    test_ap = average_precision_score(y_test, test_proba)
    print(f"\nTrain Avg Precision: {train_ap:.4f}")
    print(f"Test Avg Precision:  {test_ap:.4f}")

    # Classification report at various thresholds
    for thresh in [0.05, 0.10, 0.15]:
        print(f"\n--- Threshold: {thresh} ---")
        y_pred = (test_proba >= thresh).astype(int)
        print(classification_report(y_test, y_pred, target_names=['No Stroke', 'Stroke']))

    # Save model
    print(f"\nSaving classifier to {args.model_out}...")
    with open(args.model_out, 'wb') as f:
        pickle.dump({
            'classifier': clf,
            'embedding_model': 'all-MiniLM-L6-v2',
            'train_auc': train_auc,
            'test_auc': test_auc,
        }, f)

    # Save embeddings for later use
    print(f"Saving embeddings to {args.embeddings_out}...")
    np.save(args.embeddings_out, {
        'train': train_embeddings,
        'test': test_embeddings,
        'train_ids': [p['id'] for p in train_patients],
        'test_ids': [p['id'] for p in test_patients],
    })

    # Also compare with simple logistic regression
    print("\n" + "="*60)
    print("COMPARISON: Logistic Regression")
    print("="*60)
    lr = LogisticRegression(max_iter=1000, class_weight='balanced', random_state=42)
    lr.fit(train_embeddings, y_train)
    lr_proba = lr.predict_proba(test_embeddings)[:, 1]
    lr_auc = roc_auc_score(y_test, lr_proba)
    print(f"Logistic Regression Test ROC AUC: {lr_auc:.4f}")

    print("\n" + "="*60)
    print("DONE")
    print("="*60)


if __name__ == "__main__":
    main()
