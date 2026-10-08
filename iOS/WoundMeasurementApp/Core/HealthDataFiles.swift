import Foundation
import Darwin

/// On-device health files request exclusion from system backups. This flag is
/// guidance to the OS, not deletion of old backups or a guarantee about all copies.
/// Keep the parent excluded so atomic replacements and temporary files are covered.
enum HealthDataFiles {
    enum Failure: Error { case unexpectedFileType, backupExclusionNotApplied }

    static func prepareDirectory(_ directory: URL) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
            attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication])
        let values = try directory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        guard values.isDirectory == true, values.isSymbolicLink != true else { throw Failure.unexpectedFileType }
        try exclude(directory)
    }

    static func excludeExistingFile(_ file: URL) throws {
        let values = try file.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
        guard values.isRegularFile == true, values.isSymbolicLink != true else { throw Failure.unexpectedFileType }
        try FileManager.default.setAttributes(
            [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: file.path)
        try exclude(file)
    }

    private static func exclude(_ item: URL) throws {
        var url = item
        var values = URLResourceValues(); values.isExcludedFromBackup = true
        try url.setResourceValues(values)
        // Do not treat a successful setter as proof; read through a fresh URL.
        guard try URL(fileURLWithPath: url.path).resourceValues(forKeys: [.isExcludedFromBackupKey])
            .isExcludedFromBackup == true else { throw Failure.backupExclusionNotApplied }
    }

    /// Stage protection and exclusion before replacing the visible file. A policy
    /// failure leaves the old index intact instead of reporting failure after overwriting it.
    static func write(_ data: Data, to file: URL) throws {
        let directory = file.deletingLastPathComponent()
        try prepareDirectory(directory)
        do { try excludeExistingFile(file) }
        catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoSuchFileError { }
        let staged = directory.appendingPathComponent(".health-write-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: staged) }
        try data.write(to: staged, options: [.withoutOverwriting, .completeFileProtectionUntilFirstUserAuthentication])
        try excludeExistingFile(staged)
        // Same-directory POSIX rename atomically replaces bytes AND the staged metadata.
        // Unlike replacing first and applying attributes afterwards, there is no published
        // new file when protection/exclusion fails.
        let result = staged.withUnsafeFileSystemRepresentation { source in
            file.withUnsafeFileSystemRepresentation { destination in Darwin.rename(source!, destination!) }
        }
        guard result == 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
    }
}
