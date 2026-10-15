# Build 8B — Participant Document Management

## Scope

Build 8B adds staff-facing management of private documents associated with participant records. It supports controlled document categories, secure upload and download, metadata maintenance, qualification links, archival, replacement history, and audit records. It does not approve or verify qualifications, expose files to employers, or provide participant self-service.

## Staff workflow and access

- Staff with participant and document-view permissions can search the staff document directory and view a participant's document list and audit history.
- Upload, metadata edits, archive, and replacement require the `manage_participant_documents` permission. Category setup requires the separate `manage_participant_document_categories` permission; qualification links require `manage_qualification_document_links`. These checks run on the server for direct URL and POST requests.
- Uploads and sensitive changes require a recorded reason. Downloads are recorded in document audit history.
- Documents can be associated with qualifications belonging to the same participant only. Linking a file does not approve the qualification.

## Files and private storage

Supported formats are PDF, PNG, and JPEG, with a default maximum size of 20 MiB (configurable with `PARTICIPANT_DOCUMENT_MAX_UPLOAD_BYTES`). The upload validator checks the extension and leading file signature; it does not rely on the browser-provided MIME type. Files are stored under generated participant-document paths and are not served through Django's public static or media URL. The private storage backend refuses to generate a public URL. Authorized staff retrieve files through a permission-checked download view that sets `nosniff`, a restrictive CSP, and private no-store caching headers.

Set `PRIVATE_DOCUMENT_ROOT` to a protected directory outside the web server's public document root. If unset, development uses `private_media/participant_documents` under the project base directory; `/private_media/` is excluded by `.gitignore`. Production deployments must provision that directory with least-privilege filesystem access, encrypted backups, monitoring, and restore procedures. Do not copy private files into static assets or public media.

## History and dates

Document records include category, original filename, upload actor/time/source, content type and byte size, description, issue and expiration dates, and active/replaced/archived state. Replacements create a new record linked to the prior version; the prior file and audit events remain available. Archiving preserves the record and file. Expiration is represented by the date metadata and displayed as status; it does not automatically verify or revoke a qualification.

## Qualification links

A document may link to more than one qualification, and a qualification may have multiple linked documents. The link constraint prevents cross-participant associations. Staff must give a reason to link or unlink; both actions are recorded. Qualification records and their provenance are not changed by document replacement or archival.

## Operational limits

- This is a staff record-keeping tool, not an approval or compliance determination system.
- A valid file signature is checked, but this pass does not perform full PDF parsing or image decoding. Treat uploaded documents as untrusted content; production hardening should add maintained format decoders and malware scanning before broad deployment.
- Local private storage is not object storage and does not by itself provide high availability. Configure encrypted off-host backups and test restoration.
- Configure and verify `PRIVATE_DOCUMENT_ROOT` separately in every deployment. Keep it inaccessible to the web server's static/media handlers and preserve the same permission-checked download path.
- Existing Build 8A qualification data is preserved. The migration adds document/category/audit/link tables and does not rewrite qualification history.
