"""App and static-asset versioning.

APP_VERSION is the product release (semver). ASSET_VERSION is only a
cache-buster for /static — bump it when custom.css or vendored assets
change, independent of the app release."""
APP_VERSION = "1.0.3"
ASSET_VERSION = "22"
