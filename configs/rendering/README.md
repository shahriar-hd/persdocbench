# `configs/rendering`

Versioned configuration profiles for the **rendering** stage.

Conventions:

- One file per named profile (`<profile>.yaml`), never one mutable `default.yaml`.
- Every profile is hashed into the run manifest, so a result always names its config.
- Profiles are additive: a stage config references the profile ids it consumes.
- No secrets in this directory. Credentials come from the environment.
