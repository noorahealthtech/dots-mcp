# Create and Publish Content Design

## Goal

Add a create-only MCP capability for validating, previewing, and immediately publishing DOTS content through `POST /api/content/createAndPublishContent/:contentType`.

## Scope

Included:

- A non-mutating `preview_content_creation` MCP tool.
- A mutating `create_and_publish_content` MCP tool.
- Strict, content-type-specific validation from a local creation registry.
- Existing-tag verification through `getData`.
- Google OAuth provenance in structured MCP audit events.
- Plain JSON create requests, response parsing, citations, mock behavior, tests, and deployment documentation.
- Existing uploaded `fileData` objects may be referenced and validated without modification.

Excluded:

- `quickUpdateContent` and every form of editing or deletion.
- Media upload through `/api/media/uploadMediaToGCS`.
- Creating new tag records. Created documents may reference existing tags only.
- Overriding DOTS system metadata, including `meta.kp_contributed_by`.
- Inferring undocumented required fields, choices, or conditional rules from sampled documents.

## Verified Attribution Behavior

The live `routineVisits` document `6ac288dd870810e6b6d21048` contains two separate author values:

- `meta.kp_contributed_by` expands through getData population to the Noora user profile for Jyotiprakash Bag, including `_id`, `name`, `email`, and `profileType`. This is the linked byline shown in the page header.
- `main.author` is the plain-text Author field shown in the document's Settings section.

The create API documents `meta.kp_contributed_by` as server-generated from the user behind `x-auth-token`. The request body cannot choose it. Because this MCP uses one shared DOTS service token, DOTS will attribute every created document to that service account. Setting `main.author` does not change the linked header byline.

The OAuth bridge already preserves Google's stable `sub` in `AccessToken.subject`. It will also retain the verified email in an in-memory identity map. Every create attempt will emit a structured audit event containing the Google subject/email, content type, request hash, title, result, and returned DOTS content ID. This is MCP provenance, not DOTS contributor attribution.

## Activation And Access Policy

- Creation is disabled by default through `KMS_CREATE_ENABLED=0`.
- When enabled, the tools are registered only for Streamable HTTP with configured Google OAuth.
- Enabling creation under stdio or unauthenticated HTTP is a startup configuration error.
- Every authenticated user accepted by the existing Google-domain policy may create; there is no separate writer allowlist in this phase.
- `KMS_CREATE_CONTENT_TYPES` is a required comma-separated allowlist when creation is enabled.
- The shared DOTS account must have `PUBLISH` only for content types intentionally exposed through MCP.
- A content type must be both allowlisted and marked `commit_ready: true` in the creation registry before commit. Preview remains available for an allowlisted incomplete schema and reports its missing contract items.

## Tool Contracts

### `preview_content_creation`

Inputs:

- `content_type: str`
- `document: dict[str, Any]`

Behavior:

1. Resolve the authenticated Google actor.
2. Confirm the type is enabled.
3. Validate and normalize the candidate against the local registry.
4. Verify every tag reference with getData.
5. Return `valid`, `commit_ready`, normalized `document`, `errors`, `warnings`, actor email, and an immediate-publication warning.
6. Never call the create endpoint.

### `create_and_publish_content`

Inputs:

- `content_type: str`
- `document: dict[str, Any]`
- `confirm_publish: bool = False`

Behavior:

1. Require `confirm_publish=True`.
2. Resolve the authenticated Google actor.
3. Run the same validation and tag-verification path as preview.
4. Refuse commit when errors exist or the registry is not commit-ready.
5. POST the normalized document as ordinary JSON.
6. Annotate the returned content with `source_url`.
7. Emit success or failure audit event to stderr without logging the full document.
8. Return the created content, source URL, actor provenance, and explicit DOTS service-account attribution warning.

The confirmation boolean communicates intent but is not authorization. Authentication, deployment settings, the content-type allowlist, and registry readiness are the enforcement boundaries.

## Creation Registry

The packaged registry is separate from the existing discovery schema. Each content type stores:

- `commit_ready`
- `missing_contract`
- `fields`, keyed by exact document path
- component type
- required flag when authoritative
- tag collection and single/multi cardinality
- complete choice options when authoritative
- conditional requirements when authoritative

The initial registry transcribes every documented path and component below. It marks content types not commit-ready wherever required flags, choice options, or conditional rules are missing. The system fails closed rather than pretending sampled values are authoritative.

### Documented Fields

`learningAndSharingSessions`

- `main.title: TitleInput`
- `main.dateOfSession: DatePicker`
- `main.typeOfSession: RadioList`
- `tags.subject: TagsInputMulti`
- `tags.nooraUsers: TagsInputMulti`
- `main.presenters: TextInput`
- `main.aboutTheSession: LexicalTextEditor`
- `main.linkToDocumentation: URLInput`
- `main.uploadPresentationInPdfFormat: PDFInput`

`organisationalReports`

- `main.title: TitleInput`
- `tags.country: TagsInputMulti`
- `main.reportType: RadioList`
- `main.launchDate: DatePicker`
- `main.duration: DateRangePicker`
- `main.keyHighlights: LexicalTextEditor`
- `tags.nooraUsers: TagsInputMulti`
- `main.documentLinks: LinkEmbedWithInput`
- `main.documentInPDFFormat: PDFInput`

`programmaticAssetsTemplates`

- `main.title: TitleInput`
- `main.typeOfResource: RadioList`
- `tags.country: TagsInputSingle`
- `tags.states: TagsInputSingle`
- `tags.conditionAreas: TagsInputMulti`
- `tags.stakeholder: TagsInputMulti`
- `tags.subject: TagsInputSingle`
- `main.dateOfCompletion: DatePicker`
- `main.subjectOfTheToolkitManualStrategyGuidelinesCurricula: TextInput`
- `main.overview: LexicalTextEditor`
- `main.linkToDocument: LinkEmbedWithInput`
- `main.uploadDocumentInPdfFormat: PDFInput`
- `main.images: ImageInput`
- `main.linkToVisualDocumentation: LinkEmbedWithInput`

`programPerformanceReports`

- `main.title: TitleInput`
- `tags.country: TagsInputSingle`
- `tags.states: TagsInputSingle`
- `tags.districts: TagsInputMulti`
- `main.programName: TextInput`
- `main.reportType: RadioList`
- `main.duration: DateRangePicker`
- `main.dateOfSubmissionPresentation: DatePicker`
- `tags.nooraUsers: TagsInputMulti`
- `main.keyHighlights: LexicalTextEditor`
- `main.inputsFromExternalStakeholders: LexicalTextEditor`
- `main.waysForward: LexicalTextEditor`
- `main.documentLink: LinkEmbedWithInput`
- `main.documentInPDFFormat: PDFInput`
- `main.uploadImagesRelatedToThisActivity: ImageInput`

`reports`

- `main.title: TitleInput`
- `main.typeOfReport: RadioList`
- `main.typeOfFeedbackTestingReport: RadioList`
- `tags.country: TagsInputSingle`
- `tags.states: TagsInputMulti`
- `tags.districts: TagsInputMulti`
- `tags.facility: TagsInputMulti`
- `tags.facilityTypes: TagsInputMulti`
- `tags.conditionAreas: TagsInputMulti`
- `main.dateOfCompletionActivity: DatePicker`
- `main.titleOfTheReport: TextInput`
- `tags.stakeholder: TagsInputMulti`
- `tags.subject: TagsInputMulti`
- `tags.nooraUsers: TagsInputMulti`
- `tags.teams: TagsInputMulti`
- `main.giveASummary: LexicalTextEditor`
- `main.numberOfParticipants: NumberInput`
- `main.sessionActivityObjective: LexicalTextEditor`
- `main.keyTakeawaysFindings: LexicalTextEditor`
- `main.areaOfFocus: TextInput`
- `main.testingObjective: LexicalTextEditor`
- `main.keyTakeaways: LexicalTextEditor`
- `main.attachALinkToTheDocument: URLInput`
- `main.uploadDocumentInPDFFormat: PDFInput`
- `main.uploadImagesRelatedToTheActivity: ImageInput`
- `main.linkToVisualDocumentation: URLInput`

`researchAndEvaluationReports`

- `main.title: TitleInput`
- `main.enterTheTitleOfTheFinalDocumentManuscript: StaticRichText` (never writable)
- `tags.country: TagsInputSingle`
- `tags.states: TagsInputMulti`
- `tags.districts: TagsInputMulti`
- `tags.conditionAreas: TagsInputMulti`
- `tags.nooraUsers: TagsInputMulti`
- `tags.stakeholder: TagsInputMulti`
- `tags.subject: TagsInputMulti`
- `main.dateOfCompletion: DatePicker`
- `main.externalPartnersIfInvolved: SummaryInput`
- `main.abstract: LexicalTextEditor`
- `main.linkToDocumentOnGoogleDrive: URLInput`
- `main.linkToPublication: URLInput`
- `main.uploadDocumentInPdfFormat: PDFInput`
- `main.images: ImageInput`
- `main.linkToVisualDocumentation: URLInput`

`routineVisits`

- `main.title: TitleInput`
- `main.date: DatePicker`
- `main.visitType: CheckboxList`
- `main.author: TextInput`
- `tags.country: TagsInputSingle`
- `tags.states: TagsInputSingle`
- `tags.districts: TagsInputSingle`
- `tags.facility: TagsInputMulti`
- `tags.facilityTypes: TagsInputMulti`
- `tags.subject: TagsInputMulti`
- `tags.teams: TagsInputSingle`
- `tags.stakeholder: TagsInputMulti`
- `tags.nooraUsers: TagsInputMulti`
- `main.preLaunchPreparation: LexicalTextEditor`
- `main.attendees: LexicalTextEditor`
- `main.observationsUpdatesFromTheVisit: LexicalTextEditor`
- `main.inputsFromStakeholdersGovernmentOfficials: LexicalTextEditor`
- `main.actionables: LexicalTextEditor`
- `main.nextSteps: LexicalTextEditor`
- `main.uploadPdf: PDFInput`
- `main.uploadImagesRelatedToThisVisit: ImageInput`
- `main.linkToVideoDocumentation: URLInput`

The DOTS example explicitly identifies Date, Visit Type, Author, Country, State, District, and Stakeholders as browser-required, but the complete legal Visit Type options still need authoritative confirmation before this type is commit-ready.

`successStory`

- `main.title: TitleInput`
- `main.dateOfRecording: DatePicker`
- `tags.country: TagsInputSingle`
- `tags.states: TagsInputSingle`
- `tags.districts: TagsInputSingle`
- `tags.facility: TagsInputSingle`
- `tags.facilityTypes: TagsInputMulti`
- `tags.subject: TagsInputMulti`
- `tags.stakeholder: TagsInputMulti`
- `tags.teams: TagsInputMulti`
- `tags.nooraUsers: TagsInputMulti`
- `main.learningStory: LexicalTextEditor`
- `main.testimonials: Repeater` (unsupported and never writable)
- `main.uploadImagesRelatedToThisActivity: ImageInput`
- `main.linkToVideoDocumentation: URLInput`

`toolsAndCollaterals`

- `main.title: TitleInput`
- `tags.subject: TagsInputSingle`
- `tags.country: TagsInputSingle`
- `tags.states: TagsInputSingle`
- `tags.districts: TagsInputSingle`
- `tags.facility: TagsInputSingle`
- `tags.facilityTypes: TagsInputSingle`
- `tags.conditionAreas: TagsInputMulti`
- `main.toolsAndCollaterals_dateOfLatestVersion: DatePicker`
- `main.toolsAndCollaterals_projectName: TextInput`
- `main.toolsAndCollaterals_intendedUsecase: LexicalTextEditor`
- `main.toolsAndCollaterals_relatedToThis: LexicalTextEditor`
- `main.toolsAndCollaterals_printingSpecifications: LexicalTextEditor`
- `main.toolsAndCollaterals_approxCostPerPrintedCopy: NumberInput`
- `main.toolsAndCollaterals_linkToGoogleDriveFile: LinkEmbedWithInput`
- `main.toolsAndCollaterals_uploadDocumentInPdfFormat: PDFInput`
- `main.toolsAndCollaterals_images: ImageInput`

`trainingReports`

- `main.title: TitleInput`
- `main.typeOfReport: RadioList`
- `tags.country: TagsInputSingle`
- `tags.states: TagsInputMulti`
- `tags.districts: TagsInputMulti`
- `tags.facilityTypes: TagsInputMulti`
- `tags.conditionAreas: TagsInputMulti`
- `main.datesOfTheTraining: DateRangePicker`
- `tags.stakeholder: TagsInputMulti`
- `tags.subject: TagsInputMulti`
- `main.numberOfParticipants: NumberInput`
- `main.sessionActivityObjective: SummaryInput`
- `main.linkToDocument: URLInput`
- `main.uploadDocumentInPDFFormat: PDFInput`
- `main.uploadImages: ImageInput`

## Validation Rules

- `main.title` is required, non-empty, and a string for every content type.
- Unknown paths fail validation.
- `StaticRichText` and `Repeater` fail if supplied.
- Text/title fields require strings; URL fields additionally require `http` or `https` URLs.
- Number fields require JSON numbers and reject booleans and numeric strings.
- Date fields require timezone-bearing ISO 8601 date-times.
- Date ranges require exactly two valid date-times with start not after end.
- Radio choices require one `{value, display}` object; checkbox choices require an array of those objects. Commit fails if complete legal options are not configured.
- Single tags require exactly one item; multi tags allow zero or more. `collectionId` must match the path, and every item requires `_id`, `display`, and `tagId`.
- Tag references must exist in the referenced tag collection when queried by `_id`.
- Rich text requires `isLexical: true`, string `allText`, and a Lexical root. Extracted text from the editor state must agree with normalized `allText`.
- Image/PDF fields require arrays. Every entry must include the core successful upload fields and is preserved unchanged.
- LinkEmbed requires an object with an `http` or `https` `url`; optional metadata is preserved.
- Authoritative required and conditional rules are applied from the registry.
- Preview reports incomplete schema coverage as warnings. Commit treats incomplete coverage as an error through `commit_ready: false`.

## Error And Retry Behavior

- Local errors identify an exact path, code, expected component, and correction.
- API `errors` arrays, JSON `error` bodies, HTML errors, and non-JSON responses are parsed defensively.
- 401 distinguishes token/permission failures when the message allows it.
- 404 identifies tenant misconfiguration.
- 429 includes `RateLimit-Reset` in the model-readable error.
- Duplicate tag errors are parsed even though tag creation is out of scope.
- Create is never automatically retried after timeout or connection loss because the outcome may be ambiguous.
- A successful response must contain a `content` object.

## Audit Event

Audit events are one-line JSON written to stderr with prefix `[dots-kms-mcp.audit]`. They contain:

- `event`: `content_create`
- UTC timestamp
- `outcome`: `success` or `failure`
- Google `subject` and verified `email`
- content type
- title
- SHA-256 hash of canonical normalized request JSON
- returned DOTS content ID when successful
- HTTP status/error summary when failed

The full document, OAuth token, and KMS token are never logged.

## Recovery Boundary

There is no MCP edit tool in this phase. Incorrect published content must be corrected in the DOTS web app or through DOTS support. The optional support agreement covers troubleshooting, cleanup, and recovery but leaves data correctness with Noora.

## Testing

- Pure schema-loader and validator tests cover every component.
- Contract tests ensure every documented field above appears exactly once with the correct component.
- Preview tests prove no create call occurs.
- Commit tests prove validation is repeated and failure creates nothing.
- Wire tests assert the endpoint, headers, and ordinary JSON body.
- Mock tests prove created documents can be read back.
- OAuth tests prove Google subject/email provenance is retained.
- Audit tests prove success and failure events omit secrets and full content.
- Registration tests prove create tools are absent by default and cannot be enabled without authenticated HTTP.
