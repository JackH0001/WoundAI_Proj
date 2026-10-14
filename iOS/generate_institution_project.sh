#!/bin/sh
set -eu
cd "$(dirname "$0")"
xcodegen generate --spec project.institution.yml
mkdir -p WoundMeasurementApp.xcodeproj/project.xcworkspace/xcshareddata/swiftpm
cp Dependencies/Institution.Package.resolved WoundMeasurementApp.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved
