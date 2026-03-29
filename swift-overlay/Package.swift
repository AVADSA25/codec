// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "CODECOverlay",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "CODECOverlay",
            path: "Sources"
        )
    ],
    swiftLanguageVersions: [.v5]
)
