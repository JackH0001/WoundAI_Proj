package com.woundmeasurement.app.data.store

/** Fixed local namespaces. Existing MMH storage is deliberately unchanged. */
enum class MedicalDataScope(val databaseName: String, val imageDirectory: String, val settingsName: String) {
    MMH("wound_measurement_database", "wound_images", "woundai_settings"),
    SYNTHETIC_REVIEW("wound_measurement_database_synthetic_review_v1",
        "medical_environments/synthetic-review-v1/wound_images", "woundai_settings.synthetic-review-v1")
}
