import argparse
import json
import sys


def main():
    parser = argparse.ArgumentParser(description="Read-only TIFF channel inspector")
    parser.add_argument("image", nargs="?", help="Image path to open")
    parser.add_argument("--inspect", action="store_true", help="Print channel metadata as JSON, without opening the UI")
    args = parser.parse_args()
    if args.inspect:
        if not args.image:
            parser.error("--inspect requires an image path")
        from .reader import load_image
        try:
            print(json.dumps(load_image(args.image).report(), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"Could not read image: {exc}", file=sys.stderr)
            return 1
        return 0
    from .app import launch
    return launch(args.image)


if __name__ == "__main__":
    raise SystemExit(main())
