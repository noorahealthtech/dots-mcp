import json
from pathlib import Path

import pytest

from dots_kms_mcp.create_schema import load_create_schema


ROOT = Path(__file__).resolve().parents[1]


DOCUMENTED_FIELD_COMPONENTS = {
    "learningAndSharingSessions": {
        "main.title": "TitleInput",
        "main.dateOfSession": "DatePicker",
        "main.typeOfSession": "RadioList",
        "tags.subject": "TagsInputMulti",
        "tags.nooraUsers": "TagsInputMulti",
        "main.presenters": "TextInput",
        "main.aboutTheSession": "LexicalTextEditor",
        "main.linkToDocumentation": "URLInput",
        "main.uploadPresentationInPdfFormat": "PDFInput",
    },
    "organisationalReports": {
        "main.title": "TitleInput",
        "tags.country": "TagsInputMulti",
        "main.reportType": "RadioList",
        "main.launchDate": "DatePicker",
        "main.duration": "DateRangePicker",
        "main.keyHighlights": "LexicalTextEditor",
        "tags.nooraUsers": "TagsInputMulti",
        "main.documentLinks": "LinkEmbedWithInput",
        "main.documentInPDFFormat": "PDFInput",
    },
    "programmaticAssetsTemplates": {
        "main.title": "TitleInput",
        "main.typeOfResource": "RadioList",
        "tags.country": "TagsInputSingle",
        "tags.states": "TagsInputSingle",
        "tags.conditionAreas": "TagsInputMulti",
        "tags.stakeholder": "TagsInputMulti",
        "tags.subject": "TagsInputSingle",
        "main.dateOfCompletion": "DatePicker",
        "main.subjectOfTheToolkitManualStrategyGuidelinesCurricula": "TextInput",
        "main.overview": "LexicalTextEditor",
        "main.linkToDocument": "LinkEmbedWithInput",
        "main.uploadDocumentInPdfFormat": "PDFInput",
        "main.images": "ImageInput",
        "main.linkToVisualDocumentation": "LinkEmbedWithInput",
    },
    "programPerformanceReports": {
        "main.title": "TitleInput",
        "tags.country": "TagsInputSingle",
        "tags.states": "TagsInputSingle",
        "tags.districts": "TagsInputMulti",
        "main.programName": "TextInput",
        "main.reportType": "RadioList",
        "main.duration": "DateRangePicker",
        "main.dateOfSubmissionPresentation": "DatePicker",
        "tags.nooraUsers": "TagsInputMulti",
        "main.keyHighlights": "LexicalTextEditor",
        "main.inputsFromExternalStakeholders": "LexicalTextEditor",
        "main.waysForward": "LexicalTextEditor",
        "main.documentLink": "LinkEmbedWithInput",
        "main.documentInPDFFormat": "PDFInput",
        "main.uploadImagesRelatedToThisActivity": "ImageInput",
    },
    "reports": {
        "main.title": "TitleInput",
        "main.typeOfReport": "RadioList",
        "main.typeOfFeedbackTestingReport": "RadioList",
        "tags.country": "TagsInputSingle",
        "tags.states": "TagsInputMulti",
        "tags.districts": "TagsInputMulti",
        "tags.facility": "TagsInputMulti",
        "tags.facilityTypes": "TagsInputMulti",
        "tags.conditionAreas": "TagsInputMulti",
        "main.dateOfCompletionActivity": "DatePicker",
        "main.titleOfTheReport": "TextInput",
        "tags.stakeholder": "TagsInputMulti",
        "tags.subject": "TagsInputMulti",
        "tags.nooraUsers": "TagsInputMulti",
        "tags.teams": "TagsInputMulti",
        "main.giveASummary": "LexicalTextEditor",
        "main.numberOfParticipants": "NumberInput",
        "main.sessionActivityObjective": "LexicalTextEditor",
        "main.keyTakeawaysFindings": "LexicalTextEditor",
        "main.areaOfFocus": "TextInput",
        "main.testingObjective": "LexicalTextEditor",
        "main.keyTakeaways": "LexicalTextEditor",
        "main.attachALinkToTheDocument": "URLInput",
        "main.uploadDocumentInPDFFormat": "PDFInput",
        "main.uploadImagesRelatedToTheActivity": "ImageInput",
        "main.linkToVisualDocumentation": "URLInput",
    },
    "researchAndEvaluationReports": {
        "main.title": "TitleInput",
        "main.enterTheTitleOfTheFinalDocumentManuscript": "StaticRichText",
        "tags.country": "TagsInputSingle",
        "tags.states": "TagsInputMulti",
        "tags.districts": "TagsInputMulti",
        "tags.conditionAreas": "TagsInputMulti",
        "tags.nooraUsers": "TagsInputMulti",
        "tags.stakeholder": "TagsInputMulti",
        "tags.subject": "TagsInputMulti",
        "main.dateOfCompletion": "DatePicker",
        "main.externalPartnersIfInvolved": "SummaryInput",
        "main.abstract": "LexicalTextEditor",
        "main.linkToDocumentOnGoogleDrive": "URLInput",
        "main.linkToPublication": "URLInput",
        "main.uploadDocumentInPdfFormat": "PDFInput",
        "main.images": "ImageInput",
        "main.linkToVisualDocumentation": "URLInput",
    },
    "routineVisits": {
        "main.title": "TitleInput",
        "main.date": "DatePicker",
        "main.visitType": "CheckboxList",
        "main.author": "TextInput",
        "tags.country": "TagsInputSingle",
        "tags.states": "TagsInputSingle",
        "tags.districts": "TagsInputSingle",
        "tags.facility": "TagsInputMulti",
        "tags.facilityTypes": "TagsInputMulti",
        "tags.subject": "TagsInputMulti",
        "tags.teams": "TagsInputSingle",
        "tags.stakeholder": "TagsInputMulti",
        "tags.nooraUsers": "TagsInputMulti",
        "main.preLaunchPreparation": "LexicalTextEditor",
        "main.attendees": "LexicalTextEditor",
        "main.observationsUpdatesFromTheVisit": "LexicalTextEditor",
        "main.inputsFromStakeholdersGovernmentOfficials": "LexicalTextEditor",
        "main.actionables": "LexicalTextEditor",
        "main.nextSteps": "LexicalTextEditor",
        "main.uploadPdf": "PDFInput",
        "main.uploadImagesRelatedToThisVisit": "ImageInput",
        "main.linkToVideoDocumentation": "URLInput",
    },
    "successStory": {
        "main.title": "TitleInput",
        "main.dateOfRecording": "DatePicker",
        "tags.country": "TagsInputSingle",
        "tags.states": "TagsInputSingle",
        "tags.districts": "TagsInputSingle",
        "tags.facility": "TagsInputSingle",
        "tags.facilityTypes": "TagsInputMulti",
        "tags.subject": "TagsInputMulti",
        "tags.stakeholder": "TagsInputMulti",
        "tags.teams": "TagsInputMulti",
        "tags.nooraUsers": "TagsInputMulti",
        "main.learningStory": "LexicalTextEditor",
        "main.testimonials": "Repeater",
        "main.uploadImagesRelatedToThisActivity": "ImageInput",
        "main.linkToVideoDocumentation": "URLInput",
    },
    "toolsAndCollaterals": {
        "main.title": "TitleInput",
        "tags.subject": "TagsInputSingle",
        "tags.country": "TagsInputSingle",
        "tags.states": "TagsInputSingle",
        "tags.districts": "TagsInputSingle",
        "tags.facility": "TagsInputSingle",
        "tags.facilityTypes": "TagsInputSingle",
        "tags.conditionAreas": "TagsInputMulti",
        "main.toolsAndCollaterals_dateOfLatestVersion": "DatePicker",
        "main.toolsAndCollaterals_projectName": "TextInput",
        "main.toolsAndCollaterals_intendedUsecase": "LexicalTextEditor",
        "main.toolsAndCollaterals_relatedToThis": "LexicalTextEditor",
        "main.toolsAndCollaterals_printingSpecifications": "LexicalTextEditor",
        "main.toolsAndCollaterals_approxCostPerPrintedCopy": "NumberInput",
        "main.toolsAndCollaterals_linkToGoogleDriveFile": "LinkEmbedWithInput",
        "main.toolsAndCollaterals_uploadDocumentInPdfFormat": "PDFInput",
        "main.toolsAndCollaterals_images": "ImageInput",
    },
    "trainingReports": {
        "main.title": "TitleInput",
        "main.typeOfReport": "RadioList",
        "tags.country": "TagsInputSingle",
        "tags.states": "TagsInputMulti",
        "tags.districts": "TagsInputMulti",
        "tags.facilityTypes": "TagsInputMulti",
        "tags.conditionAreas": "TagsInputMulti",
        "main.datesOfTheTraining": "DateRangePicker",
        "tags.stakeholder": "TagsInputMulti",
        "tags.subject": "TagsInputMulti",
        "main.numberOfParticipants": "NumberInput",
        "main.sessionActivityObjective": "SummaryInput",
        "main.linkToDocument": "URLInput",
        "main.uploadDocumentInPDFFormat": "PDFInput",
        "main.uploadImages": "ImageInput",
    },
}


def _write_schema(tmp_path, content_schema):
    path = tmp_path / "create-schema.json"
    path.write_text(
        json.dumps(
            {
                "version": "test",
                "content_types": {"example": content_schema},
            }
        ),
        encoding="utf-8",
    )
    return str(path)


def test_packaged_create_schema_contains_documented_contract():
    registry = load_create_schema(None)
    actual = {
        content_type: {
            path: rule.component for path, rule in schema.fields.items()
        }
        for content_type, schema in registry.content_types.items()
    }
    assert actual == DOCUMENTED_FIELD_COMPONENTS
    routine = registry.content_types["routineVisits"]
    assert routine.fields["tags.country"].collection_id == "country"
    assert routine.fields["tags.country"].cardinality == "single"


def test_non_input_components_are_explicitly_not_writable():
    registry = load_create_schema(None)
    static = registry.content_types["researchAndEvaluationReports"].fields[
        "main.enterTheTitleOfTheFinalDocumentManuscript"
    ]
    repeater = registry.content_types["successStory"].fields["main.testimonials"]
    assert static.writable is False
    assert repeater.writable is False


def test_packaged_registry_fails_closed_until_contract_is_complete():
    registry = load_create_schema(None)
    routine = registry.content_types["routineVisits"]
    assert routine.commit_ready is False
    assert "choice_options:main.visitType" in routine.missing_contract


def test_provisional_deployment_registry_is_scoped_and_fails_closed():
    registry = load_create_schema(str(ROOT / "kms_create_schema.json"))

    assert registry.version == "provisional-2026-10-07"
    assert set(registry.content_types) == {
        "programmaticAssetsTemplates",
        "routineVisits",
    }
    for content_type, schema in registry.content_types.items():
        assert schema.commit_ready is False
        assert schema.missing_contract
        assert {
            path: rule.component for path, rule in schema.fields.items()
        } == DOCUMENTED_FIELD_COMPONENTS[content_type]

    programmatic = registry.content_types["programmaticAssetsTemplates"]
    assert programmatic.missing_contract == ("conditional_rules",)
    assert {
        path for path, rule in programmatic.fields.items() if rule.required
    } == {
        "main.title",
        "main.typeOfResource",
        "tags.country",
        "tags.conditionAreas",
        "main.dateOfCompletion",
        "main.subjectOfTheToolkitManualStrategyGuidelinesCurricula",
        "main.overview",
        "main.linkToDocument",
    }
    assert programmatic.fields["main.typeOfResource"].options == (
        ("toolkit", "Toolkit"),
        ("manual", "Manual"),
        ("strategy", "Strategy"),
        ("guidelines", "Guidelines"),
        ("curriculum", "Curriculum"),
        ("conceptNotes", "Concept Notes"),
    )

    routine = registry.content_types["routineVisits"]
    assert routine.missing_contract == ("conditional_rules",)
    assert {path for path, rule in routine.fields.items() if rule.required} == {
        "main.title",
        "main.date",
        "main.visitType",
        "main.author",
        "tags.country",
        "tags.states",
        "tags.districts",
        "tags.stakeholder",
    }
    assert routine.fields["main.visitType"].options == (
        ("facilityVisit", "Facility visit"),
        ("externalStakeholderMeet", "External stakeholder meet"),
        ("stakeholderFacilityVisit", "Stakeholder facility visit"),
        ("newLaunch", "New launch"),
        ("onlineVirtualReview", "Online/Virtual Review"),
        ("advocacyInitiative", "Advocacy Initiative"),
        ("communityVisit", "Community Visit"),
    )


def test_explicit_missing_schema_path_is_loud(tmp_path):
    missing = tmp_path / "missing.json"
    try:
        load_create_schema(str(missing))
    except FileNotFoundError as exc:
        assert str(missing) in str(exc)
    else:
        raise AssertionError("missing explicit creation schema must fail")


def test_ready_schema_rejects_nonempty_missing_contract(tmp_path):
    path = _write_schema(
        tmp_path,
        {
            "commit_ready": True,
            "missing_contract": ["required_fields"],
            "conditional_requirements": [],
            "fields": {
                "main.title": {
                    "component": "TitleInput",
                    "required": True,
                    "writable": True,
                }
            },
        },
    )

    with pytest.raises(ValueError, match="commit_ready.*missing_contract"):
        load_create_schema(path)


@pytest.mark.parametrize("component", ["RadioList", "CheckboxList"])
def test_ready_schema_rejects_choice_field_without_options(tmp_path, component):
    path = _write_schema(
        tmp_path,
        {
            "commit_ready": True,
            "missing_contract": [],
            "conditional_requirements": [],
            "fields": {
                "main.title": {
                    "component": "TitleInput",
                    "required": True,
                    "writable": True,
                },
                "main.choice": {
                    "component": component,
                    "required": False,
                    "writable": True,
                    "options": [],
                },
            },
        },
    )

    with pytest.raises(ValueError, match="main.choice.*options"):
        load_create_schema(path)


def test_external_registry_rejects_system_metadata_field(tmp_path):
    path = _write_schema(
        tmp_path,
        {
            "commit_ready": True,
            "missing_contract": [],
            "conditional_requirements": [],
            "fields": {
                "main.title": {
                    "component": "TitleInput",
                    "required": True,
                    "writable": True,
                },
                "meta.kp_contributed_by": {
                    "component": "TextInput",
                    "required": False,
                    "writable": True,
                },
            },
        },
    )

    with pytest.raises(ValueError, match="meta.kp_contributed_by.*main.*tags"):
        load_create_schema(path)


@pytest.mark.parametrize(
    ("conditional", "message"),
    [
        (
            {"path": "main.missing", "equals": "yes", "require": ["main.details"]},
            "main.missing.*configured field",
        ),
        (
            {"path": "main.readOnly", "equals": "yes", "require": ["main.details"]},
            "main.readOnly.*writable field",
        ),
        (
            {"path": "main.choice", "equals": "yes", "require": ["main.missing"]},
            "main.missing.*configured field",
        ),
        (
            {"path": "main.choice", "equals": "yes", "require": ["main.readOnly"]},
            "main.readOnly.*writable field",
        ),
    ],
)
def test_registry_rejects_invalid_conditional_paths(tmp_path, conditional, message):
    path = _write_schema(
        tmp_path,
        {
            "commit_ready": False,
            "missing_contract": ["test"],
            "conditional_requirements": [conditional],
            "fields": {
                "main.title": {
                    "component": "TitleInput",
                    "required": True,
                    "writable": True,
                },
                "main.choice": {
                    "component": "TextInput",
                    "required": False,
                    "writable": True,
                },
                "main.details": {
                    "component": "TextInput",
                    "required": False,
                    "writable": True,
                },
                "main.readOnly": {
                    "component": "StaticRichText",
                    "required": False,
                    "writable": False,
                },
            },
        },
    )

    with pytest.raises(ValueError, match=message):
        load_create_schema(path)
