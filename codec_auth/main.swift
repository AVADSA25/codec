// codec_auth — CODEC Biometric Authentication Helper
// Compile: swiftc -O -o codec_auth main.swift -framework LocalAuthentication -framework Foundation
// Usage: ./codec_auth --verify
//        ./codec_auth --check  (check if Touch ID is available)

import Foundation
import LocalAuthentication

struct AuthResult: Codable {
    let authenticated: Bool
    let method: String      // "touchID", "password", "watch", "none"
    let error: String?
    let timestamp: String
    let token: String?      // session token if authenticated
}

func generateToken() -> String {
    var bytes = [UInt8](repeating: 0, count: 32)
    _ = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
    return bytes.map { String(format: "%02x", $0) }.joined()
}

func checkAvailability() {
    let context = LAContext()
    var error: NSError?
    let available = context.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &error)

    var method = "none"
    if available {
        switch context.biometryType {
        case .touchID: method = "touchID"
        case .faceID: method = "faceID"
        case .opticID: method = "opticID"
        @unknown default: method = "biometric"
        }
    }

    let result: [String: Any] = [
        "available": available,
        "method": method,
        "error": error?.localizedDescription ?? NSNull()
    ]

    if let data = try? JSONSerialization.data(withJSONObject: result),
       let str = String(data: data, encoding: .utf8) {
        print(str)
    }
}

func verify() {
    let context = LAContext()
    context.localizedReason = "CODEC requires authentication"
    context.localizedFallbackTitle = "Use Password"
    context.localizedCancelTitle = "Cancel"

    var error: NSError?
    guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else {
        let result = AuthResult(
            authenticated: false,
            method: "none",
            error: error?.localizedDescription ?? "Biometric authentication not available",
            timestamp: ISO8601DateFormatter().string(from: Date()),
            token: nil
        )
        outputResult(result)
        return
    }

    let semaphore = DispatchSemaphore(value: 0)
    var authResult: AuthResult?

    context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Authenticate to access CODEC Dashboard") { success, authError in

        var method = "password"
        switch context.biometryType {
        case .touchID: method = "touchID"
        case .faceID: method = "faceID"
        case .opticID: method = "opticID"
        @unknown default: method = "biometric"
        }

        if success {
            authResult = AuthResult(
                authenticated: true,
                method: method,
                error: nil,
                timestamp: ISO8601DateFormatter().string(from: Date()),
                token: generateToken()
            )
        } else {
            var errorMsg = "Authentication failed"
            if let err = authError as? LAError {
                switch err.code {
                case .userCancel: errorMsg = "User cancelled"
                case .userFallback: errorMsg = "User chose password"
                case .biometryLockout: errorMsg = "Touch ID locked out — too many failed attempts"
                case .biometryNotAvailable: errorMsg = "Touch ID not available"
                case .biometryNotEnrolled: errorMsg = "No fingerprints enrolled"
                default: errorMsg = err.localizedDescription
                }
            }

            authResult = AuthResult(
                authenticated: false,
                method: method,
                error: errorMsg,
                timestamp: ISO8601DateFormatter().string(from: Date()),
                token: nil
            )
        }

        semaphore.signal()
    }

    let timeout = semaphore.wait(timeout: .now() + 60)

    if timeout == .timedOut {
        authResult = AuthResult(
            authenticated: false,
            method: "timeout",
            error: "Authentication timed out (60s)",
            timestamp: ISO8601DateFormatter().string(from: Date()),
            token: nil
        )
    }

    outputResult(authResult!)
}

func outputResult(_ result: AuthResult) {
    let encoder = JSONEncoder()
    encoder.outputFormatting = .prettyPrinted
    if let data = try? encoder.encode(result),
       let str = String(data: data, encoding: .utf8) {
        print(str)
    }
}

// ── Main ──
let args = CommandLine.arguments

if args.contains("--check") {
    checkAvailability()
} else if args.contains("--verify") {
    verify()
} else {
    print("""
    CODEC Biometric Auth Helper
    Usage:
      ./codec_auth --check    Check if Touch ID is available
      ./codec_auth --verify   Prompt for Touch ID authentication
    """)
}
