from django.db import models


class Feature(models.Model):
    name = models.CharField(max_length=255, unique=True)
    code_name = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return self.name


class Role(models.Model):
    name = models.CharField(max_length=255)
    created_by = models.ForeignKey(
        "Users",
        on_delete=models.CASCADE,
        related_name="created_roles",
        null=True,
        blank=True,
    )
    features = models.ManyToManyField(Feature, blank=True)

    def __str__(self):
        return self.name


class Users(models.Model):
    fname = models.CharField(max_length=255, null=False, blank=False)
    lname = models.CharField(max_length=255, null=False, blank=False)
    email = models.EmailField(unique=True)
    pswd = models.CharField(max_length=20, null=False, blank=False)
    cpswd = models.CharField(max_length=20, null=False, blank=False)

    # RBAC and sub-user fields
    # Removed boolean field for is_main_user, using a property instead
    created_by = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        related_name="sub_users",
        null=True,
        blank=True,
    )
    role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"

    @property
    def is_main_user(self):
        # Main users are top-level accounts (not created by another user).
        return self.created_by_id is None

    def __str__(self):
        return f"{self.fname} {self.lname}"
