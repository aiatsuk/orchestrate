.PHONY: test check score version tag

test:
	python3 -m unittest discover -s tests -p 'test_*.py' -v

check:
	python3 -m unittest tests/test_hygiene.py tests/test_version.py -v

# make score RUN=<run-dir>
score:
	python3 evals/score.py $(RUN)

version:
	@cat VERSION

# Run the tests, then create the annotated tag v<VERSION>; push it with `git push --tags`.
tag: test
	git tag -a "v$$(cat VERSION)" -m "orchestrate $$(cat VERSION)"
	@echo "tagged v$$(cat VERSION)"
