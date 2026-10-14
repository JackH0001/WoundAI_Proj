package com.woundmeasurement.app.institution

import org.junit.Assert.*
import org.junit.Test

class InstitutionIdentityPolicyTest {
    @Test fun sameAuthorizedSubjectMayRetainApplicationCredentials() {
        assertTrue(InstitutionIdentityPolicy.mayRestore("subject-a", "subject-a"))
        assertTrue(InstitutionIdentityPolicy.preserveAppCredentials("subject-a", "subject-a"))
    }
    @Test fun changedSubjectCannotRestoreOrInheritClinicalIdentity() {
        assertFalse(InstitutionIdentityPolicy.mayRestore("subject-a", "subject-b"))
        assertFalse(InstitutionIdentityPolicy.preserveAppCredentials("subject-a", "subject-b"))
    }
    @Test fun legacyOrLoggedOutIdentityCannotInheritCredentials() {
        for (prior in listOf(null, "", " ")) {
            assertFalse(InstitutionIdentityPolicy.mayRestore(prior, "subject-a"))
            assertFalse(InstitutionIdentityPolicy.preserveAppCredentials(prior, "subject-a"))
        }
    }
    @Test fun blankIdentityNeverRestoresEvenWhenEqual() {
        assertFalse(InstitutionIdentityPolicy.mayRestore("", ""))
    }
}
