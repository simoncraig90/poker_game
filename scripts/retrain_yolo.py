"""
Step 3: Retrain YOLO on the expanded dataset (PS + lab screenshots).
Uses yolov8n.pt as base to avoid bias from previous training.
"""

import os
import sys

def main():
    from ultralytics import YOLO

    dataset_yaml = os.path.join(os.path.dirname(__file__), '..', 'vision', 'dataset', 'poker.yaml')
    runs_dir = os.path.join(os.path.dirname(__file__), '..', 'vision', 'runs')
    os.makedirs(runs_dir, exist_ok=True)

    # Count dataset
    train_dir = os.path.join(os.path.dirname(__file__), '..', 'vision', 'dataset', 'images', 'train')
    val_dir = os.path.join(os.path.dirname(__file__), '..', 'vision', 'dataset', 'images', 'val')
    train_count = len([f for f in os.listdir(train_dir) if f.endswith('.png')])
    val_count = len([f for f in os.listdir(val_dir) if f.endswith('.png')])
    print(f"Dataset: {train_count} train + {val_count} val = {train_count + val_count} total")
    print(f"YAML: {dataset_yaml}")
    print()

    # Fresh start from yolov8n.pt to avoid bias
    model = YOLO('yolov8n.pt')
    print("Training from yolov8n.pt (fresh start)")

    results = model.train(
        data=os.path.abspath(dataset_yaml),
        epochs=50,
        imgsz=640,
        batch=16,
        workers=0,          # IMPORTANT: prevents zombie processes on Windows
        project=runs_dir,
        name='poker_lab',
        exist_ok=True,
        patience=15,
        save_period=10,
        plots=True,
        verbose=True,
    )

    print(f"\nTraining complete!")
    best_path = os.path.join(runs_dir, 'poker_lab', 'weights', 'best.pt')
    print(f"Best model: {best_path}")

    # Quick validation
    best_model = YOLO(best_path)
    val_results = best_model.val(data=os.path.abspath(dataset_yaml), workers=0)
    print(f"\nmAP50: {val_results.box.map50:.3f}")
    print(f"mAP50-95: {val_results.box.map:.3f}")


if __name__ == '__main__':
    main()
