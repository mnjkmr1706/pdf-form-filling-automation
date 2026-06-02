"""Pydantic models mirroring the canonical DB JSON shape.

Used as the response_format for db_template_filler so the LLM cannot
deviate from the agreed-upon DB schema. Field names match the source
schema verbatim, including its typos (modeifier, claimAdjustementReasonCode,
payerPolcityURL). The mapper consumes flat dicts via db_loader; these
classes only exist to constrain the LLM-fill step.
"""
from typing import Optional

from pydantic import BaseModel


class ServiceAdjustment(BaseModel):
    totalAmount: str = ""
    claimAdjustmentReasonCode: str = ""
    claimAdjustmentDescription: str = ""
    remittanceAdviceRemarkCode: list[str] = []
    remittanceAdviceDescription: list[str] = []


class ServiceLine(BaseModel):
    serviceDateStart: str = ""
    serviceDateEnd: str = ""
    procedureCode: str = ""
    modeifier: str = ""
    revenueCode: str = ""
    units: str = ""
    charges: str = ""
    allowedAmount: str = ""
    deductibleAmount: str = ""
    coinsuranceAmount: str = ""
    paidAmount: str = ""
    claimAdjustementReasonCode: str = ""
    claimAdjustmentDescription: str = ""
    remittanceAdviceRemarkCode: str = ""
    remittanceAdviceDescription: str = ""
    serviceAdjustment: list[ServiceAdjustment] = []


class AppealMethod(BaseModel):
    appealMethod1: Optional[str] = None
    appealMethod2: Optional[str] = None
    appealMethod3: Optional[str] = None
    clinicalAppealMethod1: Optional[str] = None
    clinicalAppealMethod2: Optional[str] = None
    clinicalAppealMethod3: Optional[str] = None


class Claim(BaseModel):
    appealsFilingDate: str = ""
    claimAuthorizationNumber: str = ""
    claimBeginningDateOfService: str = ""
    claimEndDateOfService: str = ""
    claimNumber: str = ""
    claimPaidAmount: str = ""
    claimPatientResponsibility: str = ""
    claimReasonCodes: list[int] = []
    claimServiceDate: str = ""
    claimServiceDateEnd: str = ""
    claimServiceDateStart: str = ""
    claimStatusCode: str = ""
    claimSubmissionDate: str = ""
    claimSubmittedCharges: str = ""
    clearingHouseClaimNumber: str = ""
    cobIndicator: str = ""
    dateReceived: str = ""
    denialCategory: str = ""
    denialSummary: str = ""
    denialCategoryNextBestAction: str = ""
    documentControlNumber: str = ""
    effectiveDate: str = ""
    medicalRecordNumber: str = ""
    paidAmount: str = ""
    accountBalance: str = ""
    patientAccountNumber: str = ""
    patientControlNumber: str = ""
    payerClaimNumber: str = ""
    serviceLines: list[ServiceLine] = []
    serviceType: str = ""
    statementDateFrom: str = ""
    statementDateTo: str = ""
    submittedAmount: str = ""
    typeOfClaim: str = ""
    totalDeniedChargedAmount: str = ""
    checkEftDate: str = ""
    appealMethod: AppealMethod = AppealMethod()


class Patient(BaseModel):
    dateOfBirth: str = ""
    firstName: str = ""
    gender: str = ""
    groupNumber: str = ""
    lastName: str = ""
    memberID: str = ""
    patientType: str = ""
    middleName: str = ""
    relationshipCode: str = ""
    relationshipCodeDefinition: str = ""


class Dependent(BaseModel):
    dateOfBirth: str = ""
    firstName: str = ""
    gender: str = ""
    groupNumber: str = ""
    lastName: str = ""
    memberID: str = ""
    patientRelationship: str = ""
    middleName: str = ""


class Subscriber(BaseModel):
    dateOfBirth: str = ""
    firstName: str = ""
    groupNumber: str = ""
    lastName: str = ""
    memberID: str = ""
    gender: str = ""
    relationshipCode: str = ""
    relationshipCodeDefinition: str = ""
    middleName: str = ""


class Payer(BaseModel):
    email: str = ""
    fax: str = ""
    healthPlan: str = ""
    payerExchangeID: str = ""
    payerId: str = ""
    payerName: str = ""
    payerPlanID: str = ""
    phone: str = ""
    primarySubmission: str = ""
    secondarySubmission: str = ""
    payerAddress: str = ""
    payerCity: str = ""
    payerState: str = ""
    payerZip: str = ""
    payerWebsite: str = ""
    payerPolcityURL: str = ""
    templateURL: str = ""
    formName: str = ""
    docType: str = ""
    docSize: str = ""
    templateFileName: str = ""


class Provider(BaseModel):
    clientName: str = ""
    clientID: str = ""
    clientAddress: str = ""
    billingProviderName: str = ""
    renderingProviderName: str = ""
    serviceProviderName: str = ""
    billingTin: str = ""
    billingNpi: str = ""
    billingMpin: str = ""
    billingTaxID: str = ""
    renderingTin: str = ""
    renderingNpi: str = ""
    renderingMpin: str = ""
    renderingTaxID: str = ""
    serviceProviderTin: str = ""
    serviceProviderNpi: str = ""
    serviceProviderMpin: str = ""
    serviceProviderTaxID: str = ""
    facilityName: str = ""
    facilityAddress: str = ""
    providerAddress: str = ""


class DBResult(BaseModel):
    appealInventoryId: str = ""
    claimStatusToken: str = ""
    workType: str = ""
    claim: Claim = Claim()
    dependent: Dependent = Dependent()
    patient: Patient = Patient()
    payer: Payer = Payer()
    provider: Provider = Provider()
    subscriber: Subscriber = Subscriber()
    createdBy: str = ""


class DBEnvelope(BaseModel):
    success: bool = True
    message: str = ""
    result: DBResult = DBResult()
    executionTimeSec: float = 0.0
