"""
Download real Egyptian real estate data from Kaggle using the Kaggle API.

Two datasets are supported (both from PropertyFinder Egypt):

  1. Egyptian Real Estate Listings (~20k rows, Aug 2025)
     Dataset slug: ahmedmohamed2003/egypt-real-estate-listings-on-propertyfinder
     Columns: url, price, description, location, type, size,
              bedrooms, bathrooms, available_from, payment_method, down_payment

  2. Egypt Real Estate Data 2026 (~40k rows, March 2026)
     Dataset slug: hossamelgendy/egypt-real-estate-data-2026
     Columns: Price, Location (City/Town/District/Compound),
              Bedrooms, Bathrooms, Area, Property Type,
              Furnished, GPS coords, Amenities, Agent info

Usage:
    py download_egypt_data.py [--dataset 2025|2026] [--output-dir data/real]

Prerequisites:
    1. Install Kaggle CLI:
         py -m pip install kaggle
    2. Set up your API token — download kaggle.json from
       https://www.kaggle.com/settings and place it in %USERPROFILE%\.kaggle\
    3. Accept the dataset license on the Kaggle website first.

Alternatively, download manually:
    https://www.kaggle.com/datasets/ahmedmohamed2003/egypt-real-estate-listings-on-propertyfinder
    https://www.kaggle.com/datasets/hossamelgendy/egypt-real-estate-data-2026
    Then place the CSV in data/real/ and run scripts/ingest_kaggle.py or
    scripts/ingest_kaggle_2026.py directly.
"""
import argparse
import os
import sys

DATASETS = {
    "2025": {
        "slug": "ahmedmohamed2003/egypt-real-estate-listings-on-propertyfinder",
        "description": "Egyptian Real Estate Listings (~20k rows, Aug 2025)",
        "expected_csv": "egypt_real_estate_listings.csv",
    },
    "2026": {
        "slug": "hossamelgendy/egypt-real-estate-data-2026",
        "description": "Egypt Real Estate Data 2026 (~40k rows, March 2026)",
        "expected_csv": "egypt_real_estate_2026.csv",
    },
}


def check_kaggle_setup():
    """Verify kaggle package and credentials are in place."""
    try:
        import kaggle  # noqa: F401
    except ImportError:
        print("ERROR: kaggle package not found.")
        print("Install it with:  py -m pip install kaggle")
        sys.exit(1)

    token_path = os.path.join(os.path.expanduser("~"), ".kaggle", "kaggle.json")
    if not os.path.exists(token_path):
        print("ERROR: Kaggle API credentials not found.")
        print(f"  Expected at: {token_path}")
        print("  Download your token from: https://www.kaggle.com/settings")
        sys.exit(1)

    print("✓ Kaggle credentials found.")


def download_dataset(slug: str, output_dir: str, dataset_label: str):
    import kaggle
    os.makedirs(output_dir, exist_ok=True)
    print(f"Downloading '{dataset_label}' to {output_dir} …")
    kaggle.api.authenticate()
    kaggle.api.dataset_download_files(slug, path=output_dir, unzip=True)
    print(f"✓ Downloaded to {output_dir}")


def list_downloaded_files(output_dir: str):
    files = [f for f in os.listdir(output_dir) if f.endswith(".csv")]
    if files:
        print(f"\nCSV files in {output_dir}:")
        for f in files:
            size_mb = os.path.getsize(os.path.join(output_dir, f)) / 1_048_576
            print(f"  {f}  ({size_mb:.1f} MB)")
    else:
        print(f"\nNo CSV files found in {output_dir}. Download may have failed.")


def main():
    parser = argparse.ArgumentParser(description="Download Egyptian real estate data from Kaggle.")
    parser.add_argument("--dataset", choices=["2025", "2026"], default="2026",
                        help="Which dataset to download (default: 2026, the larger/newer one)")
    parser.add_argument("--output-dir", default=os.path.join(os.path.dirname(__file__), "..", "data", "real"),
                        help="Directory to download data into (default: data/real)")
    args = parser.parse_args()

    check_kaggle_setup()

    ds = DATASETS[args.dataset]
    output_dir = os.path.abspath(args.output_dir)
    download_dataset(ds["slug"], output_dir, ds["description"])
    list_downloaded_files(output_dir)

    print(f"\nNext step — ingest into the database:")
    if args.dataset == "2026":
        print(f"  py scripts/ingest_kaggle_2026.py --csv data/real/<filename>.csv --reset-db")
    else:
        print(f"  py scripts/ingest_kaggle.py --csv data/real/{ds['expected_csv']} --reset-db")


if __name__ == "__main__":
    main()
