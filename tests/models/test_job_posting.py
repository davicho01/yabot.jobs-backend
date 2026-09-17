from sqlalchemy import inspect

from app.models.job_posting import JobPosting


def test_applications_relationship_uses_passive_deletes():
    # user_job_applications.job_posting_id is NOT NULL. Without
    # passive_deletes=True, the ORM's default behavior on deleting a
    # JobPosting is to UPDATE ... SET job_posting_id = NULL on any linked
    # UserJobApplication row, which violates that constraint and raises an
    # IntegrityError instead of deleting — verified live via
    # DELETE /admin/listings/{id} on a posting a user had saved/applied to.
    # The FK already declares ondelete="CASCADE" (see
    # UserJobApplication.job_posting_id), so passive_deletes just defers to
    # the DB instead of the ORM fighting that constraint.
    assert inspect(JobPosting).relationships["applications"].passive_deletes is True
