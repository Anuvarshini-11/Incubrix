# AI Use Disclosure

## AI-assisted development

AI assistance was used to inspect and modify this existing application, implement and test software behavior, and draft project documentation. The project-specific code and documentation are maintained in this repository and should be reviewed by the submitter.

## AI features in the application

Automatic speech recognition is an optional local feature implemented with faster-whisper. The default installation does not include its dependency or model. When enabled, recognition runs on the local machine and stores word-level timestamps in the editing session. No hosted AI provider, API credential, or external transcript service is configured by default.

The prompt-based editing assistant is deterministic pattern matching, not a generative AI model. It applies supported commands and rejects unsupported requests. Captions can also be entered manually.

## Human review

Review generated transcripts before using them for captions or filler removal. AI-assisted source changes and generated documentation should be validated with the documented tests before submission.
