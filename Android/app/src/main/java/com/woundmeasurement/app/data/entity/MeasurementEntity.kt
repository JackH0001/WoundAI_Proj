package com.woundmeasurement.app.data.entity

import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.PrimaryKey
import java.util.Date

@Entity(
    tableName = "measurements",
    foreignKeys = [
        ForeignKey(
            entity = PatientEntity::class,
            parentColumns = ["id"],
            childColumns = ["patientId"],
            onDelete = ForeignKey.CASCADE
        )
    ]
)
data class MeasurementEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val patientId: String?,
    val timestamp: Date,
    val hasWound: Boolean,
    val confidence: Double,
    val estimatedArea: Double?,
    val estimatedVolume: Double?,
    val woundType: String?,
    val quality: String,
    val processingTime: Long,
    val imagePath: String,
    val dataPath: String,
    val notes: String? = null,
    val isPatientIdentified: Boolean = patientId != null
) 