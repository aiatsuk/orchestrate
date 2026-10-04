.PHONY: test check score version release-check tag

test:
	python3 -m unittest discover -s tests -p 'test_*.py' -v

check:
	python3 -m unittest discover -s tests -p 'test_hygiene.py' -v
	python3 -m unittest discover -s tests -p 'test_version.py' -v

# make score RUN=<run-dir>
score:
	python3 evals/score.py $(RUN)

version:
	@cat VERSION

# Check that every version file and the changelog agree with VERSION.
release-check:
	python3 scripts/release.py check --tag "v$$(cat VERSION)"

# Run the tests and the release check, then create the annotated tag v<VERSION> on HEAD;
# push it with `git push origin v<VERSION>`.
tag: test release-check
	git tag -a "v$$(cat VERSION)" -m "orchestrate $$(cat VERSION)"
	@echo "tagged v$$(cat VERSION)"
