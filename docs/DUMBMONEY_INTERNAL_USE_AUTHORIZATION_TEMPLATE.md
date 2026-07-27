# DumbMoney Internal-Use Authorization — UNSIGNED TEMPLATE

**Authorization status: UNSIGNED**

This file is a template only. It is not permission, a license grant, an owner
attestation, or evidence that production use has been authorized. Release
validation must report `MISSING_SIGNED_INTERNAL_USE_AUTHORIZATION` while this
template is referenced.

## Components requiring an owner grant

- Blunder
- Dummy
- Dopey
- Doofus
- Waterboy
- Nimrod
- Dimwit

## Proposed authorization

The undersigned owner of the applicable rights authorizes the components listed
above to be copied, adapted, packaged, and run internally as part of the
DumbMoney owner-funded personal-prop system. This authorization does not grant
public sublicensing rights, authorize outside-capital operation, change
third-party licenses, or waive any obligations attached to third-party code,
data, broker services, or models.

The signer confirms that they have the authority to make this grant. If rights
are divided among multiple owners, every required owner must sign a separate
authorization or the production release remains blocked.

## Required completion

Create `docs/DUMBMONEY_INTERNAL_USE_AUTHORIZATION.md` as a separate artifact.
Copy the proposed authorization into it, replace the status with `SIGNED`, and
complete every field below. Do not edit this template into apparent evidence.

Owner legal name: [[REQUIRED_VALUE]]

Owner capacity or rights basis: [[REQUIRED_VALUE]]

Owner signature: [[REQUIRED_VALUE]]

Date (UTC): [[REQUIRED_VALUE: YYYY-MM-DD]]

Authorization status: [[REQUIRED_VALUE: SIGNED]]

After signing:

1. Hash the completed artifact with SHA-256.
2. Change the resolved fund lock’s authorization status to `SIGNED`.
3. Point the lock to the completed artifact, never this template.
4. Record its exact digest and all seven covered component names.
5. Preserve both the signed artifact and resolved fund lock with the release.

No agent may sign, date, infer, or fabricate this authorization.
