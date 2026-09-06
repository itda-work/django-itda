"""상품 모델."""

from django.db import models


class Category(models.Model):
    """상품 분류."""

    name = models.CharField('카테고리명', max_length=50, unique=True)
    slug = models.SlugField('슬러그', max_length=50, unique=True, allow_unicode=True)

    class Meta:
        verbose_name = '카테고리'
        verbose_name_plural = '카테고리'
        ordering = ['name']

    def __str__(self):
        return self.name


class Product(models.Model):
    """상품."""

    category = models.ForeignKey(
        Category,
        verbose_name='카테고리',
        on_delete=models.SET_NULL,
        null=True,
        related_name='products',
    )
    name = models.CharField('상품명', max_length=100)
    emoji = models.CharField('대표 이모지', max_length=10, default='📦')
    description = models.TextField('설명')
    price = models.PositiveIntegerField('가격 (원)')
    stock = models.PositiveIntegerField('재고')
    created_at = models.DateTimeField('등록일', auto_now_add=True)

    class Meta:
        verbose_name = '상품'
        verbose_name_plural = '상품'
        ordering = ['-created_at']

    def __str__(self):
        return f'[{self.category}] {self.name}' if self.category else self.name

    @property
    def in_stock(self):
        return self.stock > 0
