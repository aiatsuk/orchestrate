.PHONY: test check score

test:
	python3 -m unittest discover -s tests -p 'test_*.py' -v

check:
	python3 -m unittest tests/test_hygiene.py -v

# make score RUN=<run-dir>
score:
	python3 evals/score.py $(RUN)
