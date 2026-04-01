"""
Train YOLOv8 on the auto-labeled poker dataset.

Usage:
  python vision/yolo_train.py              # train from scratch
  python vision/yolo_train.py --resume     # resume training
  python vision/yolo_train.py --test       # test on val set
"""

import argparse
import os
import sys

def main():
    parser = argparse.ArgumentParser(description="Train YOLOv8 poker detector")
    parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint")
    parser.add_argument("--test", action="store_true", help="Run validation only")
    parser.add_argument("--epochs", type=int, default=100, help="Training epochs")
    parser.add_argument("--imgsz", type=int, default=640, help="Image size")
    parser.add_argument("--batch", type=int, default=8, help="Batch size")
    parser.add_argument("--model", default="yolov8n.pt", help="Base model (n/s/m/l/x)")
    args = parser.parse_args()

    from ultralytics import YOLO

    dataset_yaml = os.path.join(os.path.dirname(__file__), "dataset", "poker.yaml")
    if not os.path.exists(dataset_yaml):
        print("Dataset not found. Run yolo_label.py first.")
        sys.exit(1)

    runs_dir = os.path.join(os.path.dirname(__file__), "runs")
    os.makedirs(runs_dir, exist_ok=True)

    if args.test:
        # Find latest trained model
        best = os.path.join(runs_dir, "detect", "poker", "weights", "best.pt")
        if not os.path.exists(best):
            print(f"No trained model found at {best}")
            sys.exit(1)
        model = YOLO(best)
        results = model.val(data=dataset_yaml)
        print(f"\nmAP50: {results.box.map50:.3f}")
        print(f"mAP50-95: {results.box.map:.3f}")
        return

    if args.resume:
        last = os.path.join(runs_dir, "detect", "poker", "weights", "last.pt")
        if not os.path.exists(last):
            print(f"No checkpoint found at {last}")
            sys.exit(1)
        model = YOLO(last)
        print(f"Resuming from {last}")
    else:
        model = YOLO(args.model)
        print(f"Training from {args.model}")

    print(f"Dataset: {dataset_yaml}")
    print(f"Epochs: {args.epochs} | Image size: {args.imgsz} | Batch: {args.batch}")
    print()

    results = model.train(
        data=dataset_yaml,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        project=runs_dir,
        name="poker",
        exist_ok=True,
        workers=0,          # prevent zombie data loader processes on Windows
        patience=20,        # early stopping after 20 epochs without improvement
        save_period=10,      # save checkpoint every 10 epochs
        plots=True,
        verbose=True,
    )

    print(f"\nTraining complete!")
    print(f"Best model: {runs_dir}/detect/poker/weights/best.pt")


if __name__ == "__main__":
    main()
