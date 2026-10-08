# Agent instructions

## Delivery authorization

- After completing and verifying a requested Quota Watch change, commit the task's changes and push the current branch to its existing upstream by default, especially when the change has already been installed locally or a version has been generated. The user has given standing authorization for these ordinary commits and pushes; do not ask again unless the current request says otherwise or ownership or the upstream is unclear.
- Preserve and exclude unrelated pre-existing changes. Stop and report failed verification or a rejected push instead of forcing delivery.
- This authorization does not cover merging, Git history rewrites, force pushes, creating or pushing release tags, publishing releases, or other deployments. Those actions still require explicit approval.
